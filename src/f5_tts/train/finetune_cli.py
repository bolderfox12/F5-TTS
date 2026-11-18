import argparse
import os
import random
import re
import shutil
from copy import copy
from importlib.resources import files

import torch
from accelerate.utils import set_seed

from f5_tts.checkpoint_io import download_hf_model_file
from f5_tts.model import CFM, DiT, Trainer, UNetT
from f5_tts.model.dataset import load_dataset
from f5_tts.model.utils import get_tokenizer


# -------------------------- Dataset Settings --------------------------- #
target_sample_rate = 24000
n_mel_channels = 100
hop_length = 256
win_length = 1024
n_fft = 1024


# -------------------------- Argument Parsing --------------------------- #
def parse_args():
    parser = argparse.ArgumentParser(description="Train CFM Model")

    parser.add_argument(
        "--exp_name",
        type=str,
        default="F5TTS_v1_Base",
        choices=["F5TTS_v1_Base", "F5TTS_Base", "F5TTS_Base_bigvgan", "E2TTS_Base"],
        help="Experiment name",
    )
    parser.add_argument("--dataset_name", type=str, default="Emilia_ZH_EN", help="Name of the dataset to use")
    parser.add_argument("--learning_rate", type=float, default=1e-5, help="Learning rate for training")
    parser.add_argument("--batch_size_per_gpu", type=int, default=3200, help="Batch size per GPU")
    parser.add_argument(
        "--batch_size_type", type=str, default="frame", choices=["frame", "sample"], help="Batch size type"
    )
    parser.add_argument("--max_samples", type=int, default=64, help="Max sequences per batch")
    parser.add_argument("--num_workers", type=int, default=16, help="CPU data loader workers")
    parser.add_argument("--grad_accumulation_steps", type=int, default=1, help="Gradient accumulation steps")
    parser.add_argument("--max_grad_norm", type=float, default=1.0, help="Max gradient norm for clipping")
    parser.add_argument("--epochs", type=int, default=100, help="Number of training epochs")
    parser.add_argument("--num_warmup_updates", type=int, default=20000, help="Warmup updates")
    parser.add_argument("--save_per_updates", type=int, default=50000, help="Save checkpoint every N updates")
    parser.add_argument(
        "--keep_last_n_checkpoints",
        type=int,
        default=-1,
        help="-1 to keep all, 0 to not save intermediate, > 0 to keep last N checkpoints",
    )
    parser.add_argument("--last_per_updates", type=int, default=5000, help="Save last checkpoint every N updates")
    parser.add_argument("--finetune", action="store_true", help="Use Finetune")
    parser.add_argument("--pretrain", type=str, default=None, help="the path to the checkpoint")
    parser.add_argument(
        "--tokenizer", type=str, default="pinyin", choices=["pinyin", "char", "custom"], help="Tokenizer type"
    )
    parser.add_argument(
        "--tokenizer_path",
        type=str,
        default=None,
        help="Path to custom tokenizer vocab file (only used if tokenizer = 'custom')",
    )
    parser.add_argument(
        "--log_samples",
        action="store_true",
        help="Log inferenced samples per ckpt save updates",
    )
    parser.add_argument("--logger", type=str, default=None, choices=[None, "wandb", "tensorboard"], help="logger")
    parser.add_argument(
        "--bnb_optimizer",
        action="store_true",
        help="Use 8-bit Adam optimizer from bitsandbytes",
    )

    parser.add_argument("--dataset_path", help="Path to a prepared Studio dataset")
    parser.add_argument("--base_vocabulary_rows", type=int, help="Embedding rows expected in the base checkpoint")
    parser.add_argument("--seed", type=int, default=666)
    parser.add_argument("--validation_percent", type=int, choices=range(31), default=0)
    parser.add_argument("--activation_checkpointing", action="store_true")
    parser.add_argument("--tf32", action="store_true")
    parser.add_argument("--ram_cache_gib", type=float, default=0)
    return parser.parse_args()


# -------------------------- Training Settings -------------------------- #


def main():
    args = parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", args.dataset_name):
        raise ValueError("Invalid training dataset name.")
    mel_spec_type = "bigvgan" if args.exp_name == "F5TTS_Base_bigvgan" else "vocos"
    if args.dataset_path:
        set_seed(args.seed)
    if args.tf32:
        torch.set_float32_matmul_precision("high")
        torch.backends.cudnn.allow_tf32 = True

    checkpoint_path = str(files("f5_tts").joinpath(f"../../ckpts/{args.dataset_name}"))

    resume = (
        args.dataset_path
        and any(
            name == "model_last.pt" or (name.startswith("model_") and name.endswith(".pt"))
            for name in os.listdir(checkpoint_path)
        )
        if os.path.isdir(checkpoint_path)
        else False
    )

    # Model parameters based on experiment name

    if args.exp_name == "F5TTS_v1_Base":
        wandb_resume_id = None
        model_cls = DiT
        model_cfg = dict(
            dim=1024,
            depth=22,
            heads=16,
            ff_mult=2,
            text_dim=512,
            conv_layers=4,
        )
        if args.finetune and not resume:
            if args.pretrain is None:
                ckpt_path = str(download_hf_model_file("hf://SWivid/F5-TTS/F5TTS_v1_Base/model_1250000.safetensors"))
            else:
                ckpt_path = args.pretrain

    elif args.exp_name in ("F5TTS_Base", "F5TTS_Base_bigvgan"):
        wandb_resume_id = None
        model_cls = DiT
        model_cfg = dict(
            dim=1024,
            depth=22,
            heads=16,
            ff_mult=2,
            text_dim=512,
            text_mask_padding=False,
            conv_layers=4,
            pe_attn_head=1,
        )
        if args.finetune and not resume:
            if args.pretrain is None:
                checkpoint = "model_1250000.pt" if mel_spec_type == "bigvgan" else "model_1200000.pt"
                ckpt_path = str(download_hf_model_file(f"hf://SWivid/F5-TTS/{args.exp_name}/{checkpoint}"))
            else:
                ckpt_path = args.pretrain

    elif args.exp_name == "E2TTS_Base":
        wandb_resume_id = None
        model_cls = UNetT
        model_cfg = dict(
            dim=1024,
            depth=24,
            heads=16,
            ff_mult=4,
            text_mask_padding=False,
            pe_attn_head=1,
        )
        if args.finetune and not resume:
            if args.pretrain is None:
                ckpt_path = str(download_hf_model_file("hf://SWivid/E2-TTS/E2TTS_Base/model_1200000.pt"))
            else:
                ckpt_path = args.pretrain

    if args.finetune and not resume:
        if not os.path.isdir(checkpoint_path):
            os.makedirs(checkpoint_path, exist_ok=True)

        file_checkpoint = os.path.basename(ckpt_path)
        if not file_checkpoint.startswith("pretrained_"):  # Change: Add 'pretrained_' prefix to copied model
            file_checkpoint = "pretrained_" + file_checkpoint
        if file_checkpoint.endswith((".ckpt", ".pth")):
            file_checkpoint = os.path.splitext(file_checkpoint)[0] + ".pt"
        file_checkpoint = os.path.join(checkpoint_path, file_checkpoint)
        if not os.path.isfile(file_checkpoint):
            temporary = file_checkpoint + ".tmp"
            try:
                shutil.copy2(ckpt_path, temporary)
                os.replace(temporary, file_checkpoint)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
            print("copy checkpoint for finetune")

    # Use the tokenizer and tokenizer_path provided in the command line arguments

    tokenizer = args.tokenizer
    if tokenizer == "custom":
        if not args.tokenizer_path:
            raise ValueError("Custom tokenizer selected, but no tokenizer_path provided.")
        tokenizer_path = args.tokenizer_path
    else:
        tokenizer_path = args.dataset_name

    vocab_char_map, vocab_size = get_tokenizer(tokenizer_path, tokenizer)

    print("\nvocab : ", vocab_size)
    print("\nvocoder : ", mel_spec_type)

    mel_spec_kwargs = dict(
        n_fft=n_fft,
        hop_length=hop_length,
        win_length=win_length,
        n_mel_channels=n_mel_channels,
        target_sample_rate=target_sample_rate,
        mel_spec_type=mel_spec_type,
    )

    if args.activation_checkpointing:
        if model_cls is not DiT:
            raise ValueError("Activation checkpointing requires an F5-TTS DiT architecture.")
        model_cfg["checkpoint_activations"] = True

    model = CFM(
        transformer=model_cls(**model_cfg, text_num_embeds=vocab_size, mel_dim=n_mel_channels),
        mel_spec_kwargs=mel_spec_kwargs,
        vocab_char_map=vocab_char_map,
    )

    trainer = Trainer(
        model,
        args.epochs,
        args.learning_rate,
        num_warmup_updates=args.num_warmup_updates,
        save_per_updates=args.save_per_updates,
        keep_last_n_checkpoints=args.keep_last_n_checkpoints,
        checkpoint_path=checkpoint_path,
        batch_size_per_gpu=args.batch_size_per_gpu,
        batch_size_type=args.batch_size_type,
        max_samples=args.max_samples,
        grad_accumulation_steps=args.grad_accumulation_steps,
        max_grad_norm=args.max_grad_norm,
        logger=args.logger,
        wandb_project=args.dataset_name,
        wandb_run_name=args.exp_name,
        wandb_resume_id=wandb_resume_id,
        log_samples=args.log_samples,
        last_per_updates=args.last_per_updates,
        bnb_optimizer=args.bnb_optimizer,
        base_vocabulary_rows=args.base_vocabulary_rows,
        mel_spec_type=mel_spec_type,
    )

    if args.dataset_path:
        train_dataset = load_dataset(
            args.dataset_path, dataset_type="CustomDatasetPath", mel_spec_kwargs=mel_spec_kwargs
        )
    else:
        train_dataset = load_dataset(args.dataset_name, tokenizer, mel_spec_kwargs=mel_spec_kwargs)

    validation_dataset = None
    if args.validation_percent:
        if not args.dataset_path or len(train_dataset) < 5:
            raise ValueError("Validation requires a prepared Studio dataset with at least five examples.")
        order = list(range(len(train_dataset)))
        random.Random(args.seed).shuffle(order)
        count = max(1, round(len(order) * args.validation_percent / 100))
        validation_dataset = copy(train_dataset)
        validation_dataset.data = train_dataset.data.select(order[:count])
        validation_dataset.durations = [train_dataset.durations[i] for i in order[:count]]
        train_dataset.data = train_dataset.data.select(order[count:])
        train_dataset.durations = [train_dataset.durations[i] for i in order[count:]]
        print(f"Treino: {len(train_dataset)} exemplos · Validação: {len(validation_dataset)} exemplos", flush=True)

    if args.ram_cache_gib:
        import psutil

        workers = trainer.accelerator.num_processes
        if args.ram_cache_gib < 0:
            raise ValueError("The RAM cache budget must be positive.")
        # Snapshot each rank's available RAM before allocating any cache entries.
        cache_bytes = min(int(args.ram_cache_gib * 1024**3), psutil.virtual_memory().available // 2) // workers
        trainer.accelerator.wait_for_everyone()
        train_dataset.cache_mels_in_ram(cache_bytes)
        args.num_workers = 0
        if trainer.is_main:
            print(f"Cache mel em RAM: até {cache_bytes / 1024**3:.2f} GiB por processo.", flush=True)

    trainer.train(
        train_dataset,
        validation_dataset=validation_dataset,
        num_workers=args.num_workers,
        resumable_with_seed=args.seed,
        studio_progress=bool(args.dataset_path),
    )


if __name__ == "__main__":
    main()
