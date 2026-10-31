"""Noms de touches -> touches Windows, selon la disposition du clavier du jeu (AZERTY...).

- lettres : la touche qui produit cette lettre (« z » = la touche Z d'un clavier AZERTY) ;
- chiffres : les touches de la rangée du haut, sans Maj (en AZERTY « 3 » est la touche « " 3 ») :
  ce sont les raccourcis par défaut des cases de sorts ;
- touches nommées (escape, enter, ctrl, f1...), pavé numérique (num0 à num9) et combinaisons (« ctrl+3 »).
"""

import ctypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.VkKeyScanExW.argtypes = (ctypes.c_wchar, ctypes.c_void_p)
user32.VkKeyScanExW.restype = ctypes.c_short
user32.MapVirtualKeyExW.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
user32.MapVirtualKeyExW.restype = ctypes.c_uint
user32.keybd_event.argtypes = (ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_void_p)

MAPVK_VK_TO_VSC = 0
KEYEVENTF_KEYUP = 0x0002

NAMED = {
    "escape": 0x1B, "esc": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09, "backspace": 0x08,
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,  # flèches (31/10 : liste des types du sac)
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    **{f"num{n}": 0x60 + n for n in range(10)},  # VK_NUMPAD0 à VK_NUMPAD9
}


@dataclass(frozen=True)
class Key:
    vk: int  # code virtuel Windows
    scan: int  # code de la touche physique


def resolve(name: str, layout: int | None = None) -> list[Key]:
    """`layout` : disposition du clavier (GetKeyboardLayout) ; None = celle du bot."""
    parts = name.strip().lower().split("+")
    if not all(parts):
        raise ValueError(f"Touche invalide : {name!r}")
    return [_resolve_one(part, name, layout) for part in parts]


def _resolve_one(part: str, name: str, layout: int | None) -> Key:
    if part in NAMED:
        vk = NAMED[part]
    elif len(part) == 1 and part.isdigit():
        vk = ord(part)  # VK_0 à VK_9 : rangée du haut, quelle que soit la disposition
    elif len(part) == 1:
        code = user32.VkKeyScanExW(part, layout)
        if code == -1:
            raise ValueError(f"Aucune touche ne produit {part!r} sur ce clavier ({name!r})")
        vk = code & 0xFF
    else:
        raise ValueError(f"Touche inconnue : {part!r} dans {name!r} (exemples : escape, z, 3, ctrl+3, f1)")
    return Key(vk, user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC, layout))


def key_down(key: Key) -> None:
    user32.keybd_event(key.vk, key.scan, 0, None)


def key_up(key: Key) -> None:
    user32.keybd_event(key.vk, key.scan, KEYEVENTF_KEYUP, None)
