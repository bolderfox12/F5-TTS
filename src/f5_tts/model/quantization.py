"""Portable safetensors exports with per-output-channel INT8 linear weights."""

from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import load_file, save_file
from torch.ao.nn.quantized.dynamic import Linear as DynamicLinear
from torch.nn import Linear

from f5_tts.checkpoint_io import load_checkpoint_weights


FORMAT = "f5-studio-dynamic-int8-v1"
SCALE_SUFFIX = ".studio_int8_scale"
OBSOLETE_KEYS = ("mel_spec.mel_stft.mel_scale.fb", "mel_spec.mel_stft.spectrogram.window")


def is_int8_checkpoint(path):
    if not path or Path(path).suffix.lower() != ".safetensors":
        return False
    with safe_open(path, framework="pt") as file:
        return (file.metadata() or {}).get("format") == FORMAT


def export_int8(source, destination):
    if is_int8_checkpoint(source):
        raise ValueError("Este checkpoint já está em INT8.")
    if Path(source).suffix.lower() == ".safetensors":
        state = load_file(source)
    else:
        checkpoint = load_checkpoint_weights(source, map_location="cpu")
        state = checkpoint.get("ema_model_state_dict", checkpoint.get("model_state_dict"))
        if state is None:
            raise ValueError("O checkpoint precisa conter pesos F5-TTS ou E2-TTS.")

    exported = {}
    for key, tensor in state.items():
        key = key.removeprefix("ema_model.")
        if key in ("initted", "update", "step", *OBSOLETE_KEYS):
            continue
        if (
            key.startswith("transformer.")
            and key.endswith(".weight")
            and tensor.ndim == 2
            and (key != "transformer.text_embed.text_embed.weight")
        ):
            weight = tensor.float()
            if not torch.isfinite(weight).all():
                raise ValueError(f"Pesos inválidos em {key}.")
            scale = (weight.abs().amax(dim=1) / 127).clamp_min(torch.finfo(torch.float32).tiny)
            exported[key] = (weight / scale[:, None]).round().clamp_(-127, 127).to(torch.int8)
            exported[key + SCALE_SUFFIX] = scale
        else:
            exported[key] = tensor.contiguous()

    if not any(key.endswith(SCALE_SUFFIX) for key in exported):
        raise ValueError("Nenhuma camada linear compatível foi encontrada.")
    save_file(exported, destination, metadata={"format": FORMAT})


def int8_engine():
    engines = torch.backends.quantized.supported_engines
    current = torch.backends.quantized.engine
    if current in ("x86", "fbgemm", "onednn"):
        return current
    for engine in ("x86", "fbgemm", "onednn"):
        if engine in engines:
            return engine
    raise RuntimeError("Esta instalação do PyTorch precisa de um backend CPU x86, FBGEMM ou oneDNN para executar INT8.")


def load_int8(model, path):
    torch.backends.quantized.engine = int8_engine()
    state = load_file(path, device="cpu")
    scales = [key for key in state if key.endswith(SCALE_SUFFIX)]
    if not scales:
        raise ValueError("Checkpoint INT8 sem escalas.")
    quantized = []
    consumed = set()
    for scale_key in scales:
        weight_key = scale_key.removesuffix(SCALE_SUFFIX)
        module_name = weight_key.removesuffix(".weight")
        module = model.get_submodule(module_name)
        weight = state.pop(weight_key)
        scale = state.pop(scale_key)
        if not isinstance(module, Linear) or weight.dtype != torch.int8 or weight.shape != module.weight.shape:
            raise ValueError(f"Camada INT8 incompatível: {module_name}.")
        if scale.shape != (module.out_features,) or not torch.isfinite(scale).all() or (scale <= 0).any():
            raise ValueError(f"Escalas INT8 inválidas: {module_name}.")
        bias_key = module_name + ".bias"
        bias = state.pop(bias_key) if module.bias is not None else None
        qweight = torch._make_per_channel_quantized_tensor(
            weight, scale.to(torch.float64), torch.zeros_like(scale, dtype=torch.int64), 0
        )
        replacement = DynamicLinear(module.in_features, module.out_features, bias is not None)
        replacement.set_weight_bias(qweight, bias.float() if bias is not None else None)
        quantized.append((module_name, replacement))
        consumed.add(weight_key)
        if bias is not None:
            consumed.add(bias_key)
    result = model.load_state_dict(state, strict=False)
    if set(result.missing_keys) != consumed or result.unexpected_keys:
        raise ValueError("O checkpoint INT8 não corresponde à arquitetura ou ao vocabulário selecionado.")
    for name, replacement in quantized:
        parent, _, attribute = name.rpartition(".")
        setattr(model.get_submodule(parent), attribute, replacement)
    return model.eval()
