"""Son Wi-Fi — application Windows (Flet).

Diffuse le son du PC vers un iPhone (ou tout téléphone) par le Wi-Fi.
Lancer :  python app.py
"""
import sys
import threading
import time

import flet as ft

import son_wifi

# --- Palette -------------------------------------------------------------
FOND = "#0B1220"
CARTE = "#121A2B"
BORD = "#1E293B"
TEXTE = "#E2E8F0"
TEXTE_2 = "#94A3B8"
BLEU = "#3B82F6"
BLEU_CLAIR = "#38BDF8"
VERT = "#22C55E"
ROUGE = "#EF4444"
ORANGE = "#F59E0B"


def main(page: ft.Page):
    page.title = "Son Wi-Fi"
    page.bgcolor = FOND
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=BLEU, font_family="Segoe UI")
    page.window.width = 440
    page.window.height = 860
    page.window.min_width = 400
    page.window.min_height = 640
    page.window.icon = "icon.ico"
    page.window.prevent_close = True

    presse_papiers = ft.Clipboard()
    lanceur = ft.UrlLauncher()
    page.services.extend([presse_papiers, lanceur])

    etat = {"serveur": None, "occupe": False}

    # ------------------------------------------------------------- Widgets
    pastille_point = ft.Icon(ft.Icons.CIRCLE, size=10, color=TEXTE_2)
    pastille_texte = ft.Text("Arrêté", size=13, color=TEXTE_2, weight=ft.FontWeight.W_600)
    pastille = ft.Container(
        content=ft.Row([pastille_point, pastille_texte], spacing=8, tight=True),
        padding=ft.Padding.symmetric(horizontal=14, vertical=7),
        border_radius=999,
        bgcolor=ft.Colors.with_opacity(0.08, TEXTE_2),
        animate=ft.Animation(250, ft.AnimationCurve.EASE_OUT),
    )

    qr_image = ft.Image(src=b"", width=230, height=230, fit=ft.BoxFit.CONTAIN, visible=False,
                        gapless_playback=True)
    qr_vide = ft.Column(
        [
            ft.Icon(ft.Icons.QR_CODE_2_ROUNDED, size=64, color=ft.Colors.with_opacity(0.25, TEXTE_2)),
            ft.Text("Démarre le serveur pour\nafficher le QR code", size=13, color=TEXTE_2,
                    text_align=ft.TextAlign.CENTER),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )
    qr_chargement = ft.ProgressRing(width=36, height=36, stroke_width=3, color=BLEU, visible=False)
    qr_boite = ft.Container(
        content=ft.Stack([qr_vide, qr_image, qr_chargement], alignment=ft.Alignment.CENTER),
        width=262, height=262,
        padding=16,
        border_radius=24,
        bgcolor=ft.Colors.with_opacity(0.04, TEXTE),
        border=ft.Border.all(1, BORD),
        alignment=ft.Alignment.CENTER,
        animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
    )

    url_texte = ft.Text("—", size=15, color=TEXTE, weight=ft.FontWeight.W_600,
                        selectable=True, font_family="Consolas")
    btn_copier = ft.IconButton(ft.Icons.CONTENT_COPY_ROUNDED, icon_size=18, icon_color=TEXTE_2,
                               tooltip="Copier l'adresse", disabled=True)
    btn_ouvrir = ft.IconButton(ft.Icons.OPEN_IN_NEW_ROUNDED, icon_size=18, icon_color=TEXTE_2,
                               tooltip="Ouvrir dans le navigateur", disabled=True)
    ligne_url = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.LINK_ROUNDED, size=18, color=TEXTE_2),
                ft.Container(url_texte, expand=True),
                btn_copier, btn_ouvrir,
            ],
            spacing=6,
        ),
        padding=ft.Padding.only(left=14, right=4, top=4, bottom=4),
        border_radius=14,
        bgcolor=CARTE,
        border=ft.Border.all(1, BORD),
    )

    btn_texte = ft.Text("Démarrer", size=16, weight=ft.FontWeight.W_700, color="white")
    btn_icone = ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color="white", size=24)
    btn_principal = ft.Container(
        content=ft.Row([btn_icone, btn_texte], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        height=54,
        border_radius=16,
        gradient=ft.LinearGradient(colors=[BLEU_CLAIR, BLEU], begin=ft.Alignment.TOP_LEFT,
                                   end=ft.Alignment.BOTTOM_RIGHT),
        shadow=ft.BoxShadow(blur_radius=24, spread_radius=-6, color=ft.Colors.with_opacity(0.55, BLEU),
                            offset=ft.Offset(0, 8)),
        ink=True,
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    )

    def info(icone, titre):
        valeur = ft.Text("—", size=13, color=TEXTE, weight=ft.FontWeight.W_600,
                         max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        bloc = ft.Container(
            content=ft.Row(
                [
                    ft.Container(ft.Icon(icone, size=18, color=BLEU_CLAIR), width=36, height=36,
                                 border_radius=10, alignment=ft.Alignment.CENTER,
                                 bgcolor=ft.Colors.with_opacity(0.12, BLEU)),
                    ft.Column([ft.Text(titre, size=11, color=TEXTE_2), valeur], spacing=1,
                              expand=True),
                ],
                spacing=10,
            ),
            padding=12, border_radius=14, bgcolor=CARTE, border=ft.Border.all(1, BORD), expand=True,
        )
        return bloc, valeur

    bloc_sortie, val_sortie = info(ft.Icons.SPEAKER_ROUNDED, "Sortie audio")
    bloc_app, val_app = info(ft.Icons.PHONE_IPHONE_ROUNDED, "Appareils")
    val_app.value = "0"

    journal_liste = ft.ListView(spacing=2, auto_scroll=True, height=110)
    journal_boite = ft.Container(
        content=journal_liste, padding=12, border_radius=14, bgcolor=CARTE,
        border=ft.Border.all(1, BORD),
    )

    # ------------------------------------------------------------- Logique
    def ajouter_journal(msg):
        heure = time.strftime("%H:%M:%S")
        journal_liste.controls.append(
            ft.Text(f"{heure}  {msg}", size=11, color=TEXTE_2, font_family="Consolas", selectable=True))
        del journal_liste.controls[:-80]

    def journal_depuis_serveur(msg):
        async def maj():
            ajouter_journal(msg)
            page.update()
        page.run_task(maj)

    son_wifi.journal = journal_depuis_serveur

    def rafraichir():
        s = etat["serveur"]
        actif = bool(s and s.en_marche)
        n = s.nb_appareils if actif else 0
        if etat["occupe"]:
            pastille_point.color, pastille_texte.value = ORANGE, "Démarrage…"
        elif actif and n:
            pastille_point.color = VERT
            pastille_texte.value = f"{n} appareil{'s' if n > 1 else ''} connecté{'s' if n > 1 else ''}"
        elif actif:
            pastille_point.color, pastille_texte.value = BLEU_CLAIR, "En attente d'un appareil"
        else:
            pastille_point.color, pastille_texte.value = TEXTE_2, "Arrêté"
        pastille_texte.color = pastille_point.color
        pastille.bgcolor = ft.Colors.with_opacity(0.10, pastille_point.color)

        qr_chargement.visible = etat["occupe"]
        qr_image.visible = actif and not etat["occupe"]
        qr_vide.visible = not actif and not etat["occupe"]
        qr_boite.bgcolor = "white" if qr_image.visible else ft.Colors.with_opacity(0.04, TEXTE)
        url_texte.value = s.url if actif else "—"
        btn_copier.disabled = btn_ouvrir.disabled = not actif
        val_sortie.value = (s.appareil if actif else "—") or "—"
        val_app.value = str(n)

        if actif:
            btn_icone.icon, btn_texte.value = ft.Icons.STOP_ROUNDED, "Arrêter"
            btn_principal.gradient = ft.LinearGradient(colors=["#F87171", ROUGE],
                                                       begin=ft.Alignment.TOP_LEFT,
                                                       end=ft.Alignment.BOTTOM_RIGHT)
            btn_principal.shadow.color = ft.Colors.with_opacity(0.45, ROUGE)
        else:
            btn_icone.icon, btn_texte.value = ft.Icons.PLAY_ARROW_ROUNDED, "Démarrer"
            btn_principal.gradient = ft.LinearGradient(colors=[BLEU_CLAIR, BLEU],
                                                       begin=ft.Alignment.TOP_LEFT,
                                                       end=ft.Alignment.BOTTOM_RIGHT)
            btn_principal.shadow.color = ft.Colors.with_opacity(0.55, BLEU)
        btn_principal.opacity = 0.6 if etat["occupe"] else 1
        page.update()

    def rafraichir_depuis_fil():
        async def maj():
            rafraichir()
        page.run_task(maj)

    def demarrer():
        etat["occupe"] = True
        rafraichir_depuis_fil()
        s = son_wifi.Serveur(quand_change=rafraichir_depuis_fil)
        try:
            url = s.demarrer()
            qr_image.src = son_wifi.qr_png(url)
            etat["serveur"] = s
        except Exception as e:
            journal_depuis_serveur(f"❌ {e}")
            page.run_task(afficher_erreur, str(e))
        finally:
            etat["occupe"] = False
            rafraichir_depuis_fil()

    def arreter():
        etat["occupe"] = True
        s = etat["serveur"]
        if s:
            s.arreter()
        etat["serveur"] = None
        etat["occupe"] = False
        rafraichir_depuis_fil()

    async def afficher_erreur(msg):
        page.show_dialog(ft.SnackBar(ft.Text(msg), bgcolor=ROUGE, duration=6000))

    def clic_principal(e):
        if etat["occupe"]:
            return
        s = etat["serveur"]
        threading.Thread(target=arreter if (s and s.en_marche) else demarrer, daemon=True).start()

    async def copier(e):
        s = etat["serveur"]
        if s and s.url:
            await presse_papiers.set(s.url)
            page.show_dialog(ft.SnackBar(ft.Text("Adresse copiée ✅"), duration=2000))

    async def ouvrir(e):
        s = etat["serveur"]
        if s and s.url:
            await lanceur.launch_url(s.url)

    btn_principal.on_click = clic_principal
    btn_copier.on_click = copier
    btn_ouvrir.on_click = ouvrir

    async def fenetre(e):
        if e.type == ft.WindowEventType.CLOSE:
            s = etat["serveur"]
            if s:
                s.arreter()
            await page.window.destroy()

    page.window.on_event = fenetre

    # ------------------------------------------------------------- Mise en page
    entete = ft.Row(
        [
            ft.Image(src="icon.png", width=46, height=46),
            ft.Column(
                [
                    ft.Text("Son Wi-Fi", size=22, weight=ft.FontWeight.W_800, color=TEXTE),
                    ft.Text("Le son du PC sur ton téléphone", size=12, color=TEXTE_2),
                ],
                spacing=0,
            ),
        ],
        spacing=12,
    )

    etapes = ft.Row(
        [
            ft.Text("1. Démarre", size=12, color=TEXTE_2),
            ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=14, color=BORD),
            ft.Text("2. Scanne", size=12, color=TEXTE_2),
            ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=14, color=BORD),
            ft.Text("3. Écouter", size=12, color=TEXTE_2),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=4,
    )

    page.add(
        ft.SafeArea(
            ft.Container(
                content=ft.Column(
                    [
                        entete,
                        ft.Container(height=6),
                        ft.Row([pastille], alignment=ft.MainAxisAlignment.CENTER),
                        ft.Row([qr_boite], alignment=ft.MainAxisAlignment.CENTER),
                        etapes,
                        ligne_url,
                        btn_principal,
                        ft.Row([bloc_sortie, bloc_app], spacing=10),
                        ft.Text("Journal", size=12, color=TEXTE_2, weight=ft.FontWeight.W_600),
                        journal_boite,
                    ],
                    spacing=14,
                    scroll=ft.ScrollMode.AUTO,
                ),
                padding=ft.Padding.symmetric(horizontal=22, vertical=20),
            ),
            expand=True,
        )
    )
    ajouter_journal("Prêt. Le serveur démarre…")
    rafraichir()
    # Démarrage automatique à l'ouverture
    threading.Thread(target=demarrer, daemon=True).start()


def dossier_assets():
    # Dans l'exe (PyInstaller), les fichiers sont extraits dans sys._MEIPASS
    base = getattr(sys, "_MEIPASS", None)
    from pathlib import Path
    return str(Path(base) / "assets") if base else str(Path(__file__).resolve().parent / "assets")


if __name__ == "__main__":
    ft.run(main, assets_dir=dossier_assets())
