"""Adjustable color and geometry effects for a timeline video clip."""

import colorsys
import re

import numpy as np

DISTORTION_CENTER = {
    "x": ("Centro horizontal (%)", 0, 100, 50),
    "y": ("Centro vertical (%)", 0, 100, 50),
}
PINCH_PUNCH_RADIUS = ("Raio do efeito (%)", 10, 92, 92)

FX_CONTROLS = {
    "hsl": {
        "hue": ("Matiz (°)", -180, 180, 0),
        "saturation": ("Saturação (%)", -100, 100, 0),
        "lightness": ("Luminosidade HSL (%)", -100, 100, 0),
    },
    "invert": {"amount": ("Inversão de cores (%)", 0, 100, 100)},
    "lab": {
        "lightness": ("Luminosidade L*", -100, 100, 0),
        "green_red": ("Verde ↔ vermelho (a*)", -128, 127, 0),
        "blue_yellow": ("Azul ↔ amarelo (b*)", -128, 127, 0),
        "invert": ("Inverter luminosidade (%)", 0, 100, 0),
    },
    "gradient": {"amount": ("Intensidade do mapa (%)", 0, 100, 100)},
    "pinch": {
        "amount": ("Intensidade da contração (%)", 0, 100, 0),
        "radius": PINCH_PUNCH_RADIUS,
        **DISTORTION_CENTER,
    },
    "punch": {
        "amount": ("Intensidade da expansão (%)", 0, 100, 0),
        "radius": PINCH_PUNCH_RADIUS,
        **DISTORTION_CENTER,
    },
    "swirl": {
        "angle": ("Rotação do redemoinho (°)", -360, 360, 0),
        "radius": ("Raio do efeito (%)", 10, 100, 60),
        **DISTORTION_CENTER,
    },
}
FX_TITLES = {
    "hsl": "Ajuste HSL",
    "invert": "Inverter cores",
    "lab": "Ajuste Lab e inversão de luminosidade",
    "gradient": "Mapa de gradiente",
    "pinch": "Pinch · Contração",
    "punch": "Punch · Expansão",
    "swirl": "Redemoinho",
}
GRADIENT_COLORS = {"shadows": "#20243F", "highlights": "#F9DCA0"}
RGB_TO_XYZ = np.array(
    [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]]
)
WHITE = np.array([0.95047, 1, 1.08883])


def validate_video_effects(effects):
    if not isinstance(effects, dict) or any(name not in FX_CONTROLS for name in effects):
        raise ValueError("Escolha efeitos de vídeo disponíveis.")
    for name, values in effects.items():
        if not isinstance(values, dict) or not isinstance(values.get("enabled", False), bool):
            raise ValueError("Confira as configurações dos efeitos de vídeo.")
        for key, (_, minimum, maximum, default) in FX_CONTROLS[name].items():
            value = values.get(key, default)
            if name == "pinch" and key == "amount":
                minimum = -100  # Older projects stored expansion as a negative pinch.
            if name in ("pinch", "punch") and key == "radius":
                maximum = 150  # Accept previously saved settings and cap the radius during rendering.
            if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                raise ValueError("Confira as configurações dos efeitos de vídeo.")
        if name == "gradient" and any(
            not isinstance(values.get(key, default), str)
            or not re.fullmatch(r"#[0-9a-fA-F]{6}", values.get(key, default))
            for key, default in GRADIENT_COLORS.items()
        ):
            raise ValueError("Use uma cor hexadecimal no mapa de gradiente, como #20243F.")


def _values(effects, name):
    saved = effects.get(name, {})
    if not saved.get("enabled"):
        return None
    return {key: saved.get(key, default) for key, (_, _, _, default) in FX_CONTROLS[name].items()}


def _hsl(rgb, setting):
    hue = setting["hue"] / 360
    saturation = setting["saturation"] / 100
    lightness = setting["lightness"] / 100

    def adjust(pixel):
        original_hue, level, chroma = colorsys.rgb_to_hls(*pixel)
        level += lightness * ((1 - level) if lightness > 0 else level)
        chroma = min(1, chroma * (1 + saturation))
        return colorsys.hls_to_rgb((original_hue + hue) % 1, level, chroma)

    return np.asarray([adjust(pixel) for pixel in rgb])


def _lab(rgb, setting):
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ RGB_TO_XYZ.T / WHITE
    delta = 6 / 29
    fx, fy, fz = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29).T
    lightness = np.clip(116 * fy - 16 + setting["lightness"], 0, 100)
    invert = setting["invert"] / 100
    lightness = lightness * (1 - invert) + (100 - lightness) * invert
    a = 500 * (fx - fy) + setting["green_red"]
    b = 200 * (fy - fz) + setting["blue_yellow"]
    fy = (lightness + 16) / 116
    values = np.column_stack((fy + a / 500, fy, fy - b / 200))
    xyz = np.where(values > delta, values**3, 3 * delta**2 * (values - 4 / 29)) * WHITE
    linear = np.clip(xyz @ np.linalg.inv(RGB_TO_XYZ).T, 0, 1)
    return np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)


def _gradient(rgb, effects, setting):
    def color(key):
        value = effects["gradient"].get(key, GRADIENT_COLORS[key])
        return np.asarray([int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)])

    shadows, highlights = color("shadows"), color("highlights")
    weight = (rgb @ np.array([0.2126, 0.7152, 0.0722]))[:, None]
    strength = setting["amount"] / 100
    return rgb * (1 - strength) + (shadows + weight * (highlights - shadows)) * strength


def _write_lut(path, effects, hsl, invert, lab, gradient):
    size = 33
    axis = np.linspace(0, 1, size)
    blue, green, red = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.column_stack((red.ravel(), green.ravel(), blue.ravel()))
    if hsl:
        rgb = _hsl(rgb, hsl)
    if invert:
        amount = invert["amount"] / 100
        rgb = rgb * (1 - amount) + (1 - rgb) * amount
    if lab:
        rgb = _lab(rgb, lab)
    if gradient:
        rgb = _gradient(rgb, effects, gradient)
    with path.open("w", encoding="ascii") as stream:
        stream.write(f"LUT_3D_SIZE {size}\n")
        np.savetxt(stream, np.clip(rgb, 0, 1), fmt="%.7f")


def _geometry(setting, *, swirl=False, punch=False):
    radius = (setting["radius"] if swirl else min(setting["radius"], PINCH_PUNCH_RADIUS[2])) / 100
    center_x, center_y = setting["x"] / 100, setting["y"] / 100
    prefix = (
        f"st(0,2*(X/W-{center_x}));st(1,2*(Y/H-{center_y}));st(2,hypot(ld(0),ld(1)));st(3,max(0,1-ld(2)/{radius}));"
    )
    if swirl:
        angle = np.deg2rad(setting["angle"])
        prefix += f"st(4,{angle:.8f}*ld(3)*ld(3));"
        dx = "ld(0)*cos(ld(4))-ld(1)*sin(ld(4))"
        dy = "ld(0)*sin(ld(4))+ld(1)*cos(ld(4))"
    else:
        amount = setting["amount"] / (-100 if punch else 100)
        prefix += f"st(4,exp({amount:.8f}*ld(3)*2));"
        dx, dy = "ld(0)*ld(4)", "ld(1)*ld(4)"
    x = f"clip(W*({center_x}+({dx})/2),0,W-1)"
    y = f"clip(H*({center_y}+({dy})/2),0,H-1)"
    return "geq=" + ":".join(f"{channel}='{prefix}{channel}({x},{y})'" for channel in "rgb")


def video_effect_filters(effects, directory, index):
    if not effects:
        return []
    validate_video_effects(effects)
    hsl, invert, lab, gradient = (_values(effects, key) for key in ("hsl", "invert", "lab", "gradient"))
    pinch, punch, swirl = (_values(effects, key) for key in ("pinch", "punch", "swirl"))
    filters = []
    if hsl or (invert and invert["amount"]) or lab or gradient:
        name = f"fx-{index}.cube"
        _write_lut(directory / name, effects, hsl, invert, lab, gradient)
        filters.extend(("format=gbrp", f"lut3d=file={name}"))
    if (pinch and pinch["amount"]) or (punch and punch["amount"]) or (swirl and swirl["angle"]):
        filters.append("format=gbrp")
        if pinch and pinch["amount"]:
            filters.append(_geometry(pinch))
        if punch and punch["amount"]:
            filters.append(_geometry(punch, punch=True))
        if swirl and swirl["angle"]:
            filters.append(_geometry(swirl, swirl=True))
    if filters:
        filters.append("format=yuv420p")
    return filters
