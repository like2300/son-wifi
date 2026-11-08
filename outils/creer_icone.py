"""Génère l'icône bleue de Son Wi-Fi (assets/icon.png, assets/icon.ico, docs/icon.png).

    python outils/creer_icone.py
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

RACINE = Path(__file__).resolve().parent.parent
T = 1024  # dessin en grand, puis réduction (bords lisses)


def degrade(taille, haut, bas):
    """Dégradé diagonal entre deux couleurs."""
    img = Image.new("RGB", (taille, taille))
    px = img.load()
    for y in range(taille):
        for x in range(taille):
            t = (x * 0.35 + y * 0.65) / taille
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(haut, bas))
    return img


def icone():
    petit = 256
    fond = degrade(petit, (56, 189, 248), (29, 78, 216)).resize((T, T), Image.BICUBIC)

    # Carré aux coins arrondis
    masque = Image.new("L", (T, T), 0)
    ImageDraw.Draw(masque).rounded_rectangle((40, 40, T - 40, T - 40), radius=230, fill=255)

    # Haut-parleur + ondes (blanc)
    dessin = Image.new("L", (T, T), 0)
    d = ImageDraw.Draw(dessin)
    cy = T // 2
    d.rounded_rectangle((235, cy - 95, 345, cy + 95), radius=26, fill=255)          # corps
    d.polygon([(320, cy - 95), (500, cy - 225), (500, cy + 225), (320, cy + 95)], fill=255)  # pavillon
    d.rounded_rectangle((470, cy - 225, 520, cy + 225), radius=25, fill=255)
    for r, w in ((120, 52), (215, 52), (310, 52)):                                    # ondes
        box = (530 - r, cy - r, 530 + r, cy + r)
        d.arc(box, start=-48, end=48, fill=255, width=w)
        # bouts arrondis
        import math
        for a in (-48, 48):
            ex = 530 + (r - w / 2) * math.cos(math.radians(a))
            ey = cy + (r - w / 2) * math.sin(math.radians(a))
            d.ellipse((ex - w / 2, ey - w / 2, ex + w / 2, ey + w / 2), fill=255)

    blanc = Image.new("RGB", (T, T), (255, 255, 255))
    img = Image.composite(blanc, fond, dessin).convert("RGBA")
    img.putalpha(masque)

    # Légère ombre portée sous le carré
    ombre = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    om = masque.point(lambda v: v * 0.25)
    ombre.putalpha(ImageChops.offset(om, 0, 14))
    return Image.alpha_composite(ombre, img)


def main():
    img = icone()
    (RACINE / "assets").mkdir(exist_ok=True)
    (RACINE / "docs").mkdir(exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(RACINE / "assets" / "icon.png")
    img.resize((512, 512), Image.LANCZOS).save(RACINE / "docs" / "icon.png")
    img.resize((64, 64), Image.LANCZOS).save(RACINE / "docs" / "favicon.png")
    img.save(RACINE / "assets" / "icon.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icône créée : assets/icon.png, assets/icon.ico, docs/icon.png, docs/favicon.png")


if __name__ == "__main__":
    main()
