"""Qui doit jouer ? D'après les notifications de Windows que Dofus Rétro envoie au début du tour d'un personnage
(team.turn_signal: notifications, ou --notifications ; par défaut, la timeline seule : team/turns.py).

Option du jeu « Notifications en arrière-plan » : au début du tour d'un personnage dont la fenêtre n'est pas devant,
le jeu envoie une notification Windows (aussi pour d'autres sujets : invitation de groupe, échange, message...).
Toutes les fenêtres du jeu ont le même identifiant d'application (AppUserModelID com.dofus.d1elauncher) : seul le
texte dit de quel personnage il s'agit, et de quoi. Une notification n'est un SIGNAL de tour que si elle nomme UN
personnage de l'équipe ET contient un mot de tour (team.turn_words, TURN_WORDS par défaut ; sans accents ni
majuscules, en mot entier, dans tout son texte ; un seul suffit ; {nom} : le personnage nommé). Texte du jeu, vu le
03/10 : titre « Walitcoin - Dofus Retro v1.49.5 », texte « C'est à Walitcoin de jouer » ; autre exemple vu : « Succès
déverrouillé : Bouftous d'Incarnam. » (pas un tour).

Lecture par l'API de Windows (UserNotificationListener.GetNotificationsAsync) dans un fil à part : aucune capture,
aucune fenêtre, aucun clic. Relevé toutes les 0,15 s pendant l'attente d'un tour (relevé actif) ; sinon, toutes les
secondes, pour l'enregistrement seulement (aucun relevé hors de l'attente d'un tour sans fichier d'enregistrement). Les
bannières peuvent être désactivées dans Windows, et le centre de notifications aussi : l'API les lit quand même (03/10 :
17 notifications de tour sur 17, lues 0,03 à 0,64 s après leur création). Ni paquet MSIX ni certificat : l'abonnement par événement en demanderait (identité de paquet), le relevé
périodique non ; Windows doit seulement autoriser l'accès aux notifications.

Enregistrement (`record` : notifications.jsonl du dossier de session logs/<date_heure>/, à côté de bot.log) : TOUTES
les notifications de Dofus relevées, pendant les tours ou non, signaux ou ignorées, une ligne JSON chacune, une seule
fois par Id, pour connaître leurs textes après un test :
  {"lue": "2026-10-03T12:30:05.123+02:00", "creee": ... (null : illisible), "id": 123, "application": "...",
   "textes": ["titre", "ligne"...], "personnage": "Nom" ou null, "noms": [personnages de l'équipe nommés],
   "mot_de_tour": "tour" ou null, "decision": "signal"..., "releve_actif": true}
Celles des autres applications (mails, messageries...) ne sont ni gardées ni écrites. Une écriture ratée n'arrête pas
l'écoute (un avertissement, la première fois).

Contrat avec le TeamBot (team/loop.py) :
- `enabled_for_polling` : relevé actif seulement quand il est vrai ; le TeamBot l'active en combat quand il attend un
  tour, le coupe pendant le tour d'un personnage (`playing`) et hors combat. Relevé coupé : aucun signal. Une
  notification lue relevé coupé qui en serait un (celle du personnage suivant, envoyée pendant la fin du tour du
  précédent) attend le relevé actif suivant (ou `take`) : elle y est jugée comme si elle était lue à ce moment-là,
  ainsi le relevé lent ne fait rien perdre ;
- `new_fight()` au début des tours d'un combat, `playing(nom)` / `played(nom)` autour du tour d'un personnage ;
- `take(noms)` : le dernier personnage signalé parmi ceux du combat (ou None), consommé. Ce n'est qu'un INDICE : le
  TeamBot va d'abord dans sa fenêtre, et n'y joue que si le timer du tour y est affiché.
Décisions (journal et enregistrement) : « signal » ; ignorées : « sans nom de l'équipe », « plusieurs noms »,
« sans mot de tour » (un personnage nommé, mais pas son tour), « ancienne » (d'avant le démarrage du bot ou le combat),
« d'avant son dernier tour » (la sienne, lue en retard) ; « hors relevé » : lue relevé coupé, jugée au relevé actif
suivant. Les notifications d'autres applications et celles déjà vues (même Id) sont écartées sans décision.

Paquets pywinrt (PyPI), importés seulement dans le fil d'écoute : absents, ou accès refusé par Windows, l'écouteur se
désactive avec un message qui dit quoi faire, et le bot suit la timeline comme d'habitude.

Diagnostic à lancer soi-même, le jeu ouvert (n'agit sur rien, affiche chaque notification de Dofus, le personnage
reconnu et la décision, avec les mots de tour de config/settings.yaml, et les enregistre toutes dans
logs/notifications_diagnostic_<date_heure>.jsonl) : .venv\\Scripts\\python -m bot.team.signals Jesuisleking Imthebest
"""

import asyncio
import ctypes
import importlib.metadata
import json
import logging
import re
import sys
import threading
import time
import unicodedata
import winreg
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot.config import ROOT, load_settings

log = logging.getLogger(__name__)

TIMELINE, NOTIFICATIONS = "timeline", "notifications"  # valeurs de team.turn_signal
DOFUS_APP = "com.dofus.d1elauncher"  # AppUserModelID de Dofus Rétro (raccourci « Dofus 1 »), le même pour ses fenêtres
# team.turn_words : texte des notifications de tour, vu le 03/10 (17 tours sur 17) : titre « Walitcoin - Dofus Retro
# v1.49.5 », texte « C'est à Walitcoin de jouer ». {nom} : le personnage nommé par la notification, à cette place.
NAME = "{nom}"
TURN_WORDS = ("c'est à {nom} de jouer",)
RECORD_NAME = "notifications.jsonl"  # enregistrement de toutes les notifications de Dofus, dans le dossier de session
POLL_EVERY = 0.15  # secondes entre deux relevés, pendant l'attente d'un tour
IDLE_POLL = 1.0  # relevé coupé : un relevé par seconde, pour l'enregistrement seulement (réactivé, il repart aussitôt)
# Le premier tour a pu commencer juste avant que le TeamBot ne voie la timeline (_wait_combat regarde toutes les
# 0,3 s) ; les notifications hors combat (invitations, échanges) sont bien plus anciennes (placement, Prêt...).
FIGHT_START_MARGIN = 3.0
START_WAIT = 10.0  # secondes au plus pour charger WinRT et obtenir l'accès aux notifications
API_TIMEOUT = 5.0  # secondes au plus pour une opération de l'API (relevé, demande d'accès)
WINDOWS_EPOCH = 11_644_473_600  # secondes entre le 01/01/1601 (heure WinRT) et le 01/01/1970 (time.time)

PACKAGES = ("winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections",
            "winrt-Windows.UI.Notifications", "winrt-Windows.UI.Notifications.Management",
            "winrt-Windows.ApplicationModel")
# Versions et empreintes épinglées (provenance vérifiée le 03/10/2026 : organisation PyPI pywinrt, sans attestation).
PIP = ".venv\\Scripts\\python -m pip install --require-hashes --only-binary=:all: -r requirements-winrt.txt"
WINDOWS_SETUP = (
    "Windows : Paramètres > Confidentialité et sécurité > Notifications, « Accès aux notifications » et « Autoriser "
    "les applications à accéder à vos notifications » activés ; Paramètres > Système > Notifications > Dofus : "
    "notifications activées, « Afficher les bannières de notification » décoché (elles couvriraient la barre de "
    "sorts). Jeu : option « Notifications en arrière-plan » activée (à vérifier dans chaque fenêtre)."
)

SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"  # un sous-dossier par application
PUSH_KEY = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"  # ToastEnabled : toutes les notifications

# Décision pour chaque notification de Dofus (journal et enregistrement)
SIGNAL = "signal"  # tour d'un personnage de l'équipe : sa fenêtre regardée d'abord
HELD = "hors relevé"  # en serait un, mais lue relevé coupé : jugée au relevé actif suivant
NO_NAME, MANY_NAMES = "sans nom de l'équipe", "plusieurs noms"
NO_TURN_WORD = "sans mot de tour"  # un personnage nommé, mais pas son tour : invitation, échange, message...
OLD = "ancienne"  # d'avant le démarrage du bot ou le combat
BEFORE_LAST_TURN = "d'avant son dernier tour"  # la sienne, lue après la fin de son tour


def turn_signal(settings: dict, forced: bool = False) -> str:
    """Réglage team.turn_signal (« timeline » par défaut) ; `forced` : option --notifications."""
    if forced:
        return NOTIFICATIONS
    value = str((settings.get("team") or {}).get("turn_signal") or TIMELINE).strip().lower()
    if value not in (TIMELINE, NOTIFICATIONS):
        raise ValueError(f"team.turn_signal doit valoir {TIMELINE} ou {NOTIFICATIONS} (pas {value!r})")
    return value


def turn_words(settings: dict) -> tuple[str, ...]:
    """Réglage team.turn_words (TURN_WORDS par défaut) : un mot ou une expression, ou une liste."""
    value = (settings.get("team") or {}).get("turn_words")
    if value is None:
        return TURN_WORDS
    if not isinstance(value, (list, tuple)):
        value = [value]
    words = tuple(str(w).strip() for w in value if _fold(str(w)).strip())
    if not words:
        raise ValueError("team.turn_words : au moins un mot ou une expression (sinon aucune notification n'est un tour)")
    return words


@dataclass(frozen=True)
class Toast:
    """Une notification du centre de notifications."""

    id: int
    app: str  # AppUserModelID de l'application qui l'a envoyée
    texts: tuple[str, ...]  # titre, puis lignes du texte
    created: float  # heure de création, comme time.time() ; 0 : illisible (comptée comme ancienne)


def _fold(text: str) -> str:
    """Sans accents ni majuscules, apostrophes droites : « Élodie » -> « elodie », « C’est » -> « c'est »."""
    text = text.replace("’", "'")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _pattern(words: str, name: str | None = None) -> str | None:
    """Mot ou expression en mots entiers, sans accents ni majuscules (à chercher dans un texte passé par _fold) ;
    n'importe quels espaces entre ses mots. {nom} y est remplacé par `name` ; None si l'expression en a besoin et
    qu'aucun personnage n'est nommé."""
    parts = []
    for part in _fold(words).split():
        if part == NAME:
            if name is None:
                return None
            parts += [re.escape(p) for p in _fold(name).split()]
        else:
            parts.append(re.escape(part))
    return r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)"


def _named(text: str, names: Iterable[str]) -> list[str]:
    folded = _fold(text)
    return [n for n in names if re.search(_pattern(n), folded)]


def names_in(texts: tuple[str, ...] | list[str], names: Iterable[str]) -> list[str]:
    """Personnages de l'équipe nommés dans la notification : ceux de son titre, sinon ceux de tout son texte (mot
    entier, sans tenir compte des accents ni des majuscules)."""
    names = list(names)
    return _named(" ".join(texts[:1]), names) or _named(" ".join(texts), names)


def character_in(texts: tuple[str, ...] | list[str], names: Iterable[str]) -> str | None:
    """LE personnage de l'équipe nommé dans la notification (names_in). None si aucun, ou plusieurs (invitation de
    l'un à l'autre, par exemple)."""
    found = names_in(texts, names)
    return found[0] if len(found) == 1 else None


def turn_word_in(texts: tuple[str, ...] | list[str], words: Iterable[str], name: str | None = None) -> str | None:
    """Le premier mot de tour (team.turn_words) trouvé dans tout le texte de la notification (mot entier, sans tenir
    compte des accents ni des majuscules ; {nom} : `name`, le personnage nommé) ; None si aucun."""
    folded = _fold(" ".join(texts))
    return next((w for w in words if (p := _pattern(w, name)) is not None and re.search(p, folded)), None)


def _when(seconds: float) -> str | None:
    """Heure locale ISO, à la milliseconde ; None si inconnue (0) ou illisible."""
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone().isoformat(timespec="milliseconds")
    except (OverflowError, OSError, ValueError):
        return None


class Recording:
    """Fichier d'enregistrement des notifications de Dofus : une ligne JSON par notification, ajoutée aussitôt (rien de
    perdu si le bot s'arrête net). Une écriture ratée n'arrête pas l'écoute : un avertissement, la première fois."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.failures = 0

    def write(self, entry: dict) -> bool:
        try:
            line = json.dumps(entry, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(line + "\n")
            return True
        except (OSError, TypeError, ValueError):
            self.failures += 1
            if self.failures == 1:
                log.warning("Enregistrement des notifications impossible dans %s (l'écoute continue, je réessaie à "
                            "chaque notification)", self.path, exc_info=True)
            return False


# Réglages de Windows (registre, lecture seule)


def _registry(path: str) -> dict[str, object] | None:
    """Valeurs d'une clé de HKEY_CURRENT_USER ; None si la clé n'existe pas."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            values, index = {}, 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    return values
                values[name] = value
                index += 1
    except OSError:
        return None


def settings_problems(app: dict | None, push: dict | None) -> list[str]:
    """Ce qui empêche de lire les notifications de Dofus, ou les fait couvrir le jeu. `app` : valeurs de la clé de
    Dofus (None : Windows n'en a encore reçu aucune) ; `push` : réglage général. Valeur absente : celle par défaut
    (activé)."""
    problems = []
    if push is not None and push.get("ToastEnabled", 1) == 0:
        problems.append("toutes les notifications sont coupées (Paramètres > Système > Notifications)")
    if app is None:
        problems.append("Windows n'a encore reçu aucune notification de Dofus : active l'option du jeu « Notifications "
                        "en arrière-plan »")
        return problems
    if app.get("Enabled", 1) == 0:
        problems.append("notifications de Dofus coupées (Paramètres > Système > Notifications > Dofus : active-les)")
    # ShowInActionCenter à 0 (pas gardées dans le centre de notifications) n'empêche pas de les lire : 03/10, 17
    # notifications de tour sur 17 lues, 0,03 à 0,64 s après leur création.
    if app.get("ShowBanner", 1) != 0:
        problems.append("bannières de Dofus affichées : elles couvriraient la barre de sorts (décoche « Afficher les "
                        "bannières de notification »)")
    return problems


def check_windows_settings(app: str = DOFUS_APP) -> list[str]:
    """Avertit (un message) si les réglages de Windows empêchent de lire les notifications de Dofus."""
    problems = settings_problems(_registry(rf"{SETTINGS_KEY}\{app}"), _registry(PUSH_KEY))
    if problems:
        log.warning("Notifications de Dofus dans Windows : %s", " ; ".join(problems))
    return problems


def missing_packages() -> list[str]:
    """Paquets pywinrt absents (d'après les paquets installés, sans rien importer)."""
    missing = []
    for name in PACKAGES:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return missing


# Lecture par l'API de Windows (WinRT)


def _join_mta() -> None:
    """Le fil d'écoute rejoint l'appartement multithread de COM : dans un appartement monothread sans boucle de
    messages, les opérations asynchrones de WinRT ne se terminent jamais. Déjà fait : sans effet. (Pas de
    RoUninitialize à la fin : des objets WinRT gardés en cache seraient libérés après.)"""
    try:
        ctypes.WinDLL("combase").RoInitialize(1)  # RO_INIT_MULTITHREADED
    except (OSError, AttributeError):
        pass


def _static(cls, name: str):
    """Propriété statique WinRT : attribut de la classe, ou get_<nom>() selon la version de pywinrt."""
    value = getattr(cls, name, None)
    if value is None:
        getter = getattr(cls, f"get_{name}", None)
        value = getter() if getter is not None else None
    return value


def _seconds(value) -> float:
    """Heure de création WinRT -> secondes comme time.time() ; 0 si illisible."""
    try:
        if hasattr(value, "timestamp"):
            if getattr(value, "tzinfo", None) is None:
                value = value.replace(tzinfo=timezone.utc)  # heure universelle, sans fuseau
            return float(value.timestamp())
        return int(value.universal_time) / 1e7 - WINDOWS_EPOCH  # centaines de ns depuis 1601
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return 0.0


class WindowsNotifications:
    """Centre de notifications de Windows, lu par UserNotificationListener (paquets pywinrt). Créé et utilisé dans le
    seul fil d'écoute : les opérations asynchrones de WinRT y sont attendues par une boucle asyncio à lui. Ne rend que
    les NOUVELLES notifications de `app` (Id pas encore rendu)."""

    def __init__(self, app: str = DOFUS_APP) -> None:
        _join_mta()
        # Importations ici : le bot reste utilisable sans ces paquets, et ne les charge qu'en mode notifications.
        import winrt.windows.applicationmodel  # noqa: F401 — AppInfo : identifiant de l'application
        import winrt.windows.foundation  # noqa: F401 — opérations asynchrones
        import winrt.windows.foundation.collections  # noqa: F401 — listes rendues par l'API
        from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
        from winrt.windows.ui.notifications.management import (UserNotificationListener,
                                                               UserNotificationListenerAccessStatus)

        self.app = app.casefold()
        self._toast = NotificationKinds.TOAST
        self._allowed = UserNotificationListenerAccessStatus.ALLOWED
        self._generic = _static(KnownNotificationBindings, "toast_generic")
        self._listener = _static(UserNotificationListener, "current")
        self._loop = asyncio.new_event_loop()
        self._done: set[int] = set()  # Id déjà rendus ou écartés (autre application) : plus relus

    def _wait(self, operation):
        async def finished():
            return await operation

        return self._loop.run_until_complete(asyncio.wait_for(finished(), API_TIMEOUT))

    def access(self) -> str | None:
        """None si l'accès aux notifications est accordé ; sinon le statut donné par Windows."""
        try:
            status = self._listener.get_access_status()
        except Exception:  # noqa: BLE001 — on demande l'accès ci-dessous, qui dira pourquoi
            status = None
        if status != self._allowed:
            # Application non empaquetée : décidé par les réglages de confidentialité, sans fenêtre de demande.
            status = self._wait(self._listener.request_access_async())
        return None if status == self._allowed else str(getattr(status, "name", status))

    def read(self) -> list[Toast]:
        notes = self._wait(self._listener.get_notifications_async(self._toast))
        present, toasts = set(), []
        for note in notes:
            try:
                note_id = int(note.id)
                present.add(note_id)
                if note_id in self._done:
                    continue
                self._done.add(note_id)
                app = str(note.app_info.app_user_model_id)
                if app.casefold() == self.app:
                    toasts.append(Toast(note_id, app, self._texts(note), _seconds(note.creation_time)))
            except Exception:  # noqa: BLE001 — une notification illisible ne doit pas empêcher de lire les autres
                log.debug("Notification illisible", exc_info=True)
        self._done &= present  # celles qui ont quitté le centre de notifications ne reviendront pas
        return toasts

    def _texts(self, note) -> tuple[str, ...]:
        """Titre et lignes de texte : modèle ToastGeneric, sinon tous les modèles de la notification."""
        visual = note.notification.visual
        binding = visual.get_binding(self._generic) if self._generic else None
        bindings = [binding] if binding is not None else list(visual.bindings)
        return tuple(str(t.text) for b in bindings for t in b.get_text_elements() if t.text)

    def close(self) -> None:
        self._loop.close()


# Écouteur


class TurnSignals:
    """Écouteur des notifications de Dofus : contrat dans la docstring du module."""

    def __init__(self, names: Iterable[str], interval: float = POLL_EVERY, app: str = DOFUS_APP,
                 reader: Callable[[], object] | None = None, clock: Callable[[], float] = time.time,
                 verbose: bool = False, words: Iterable[str] = TURN_WORDS, record: Path | str | None = None,
                 idle: float = IDLE_POLL) -> None:
        """`names` : personnages de l'équipe. `reader` : fabrique du lecteur, appelée dans le fil d'écoute (défaut :
        WindowsNotifications ; tests : faux lecteur, avec access(), read() et close()). `clock` : heure, dans la même
        échelle que l'heure de création des notifications. `verbose` : chaque notification de Dofus au journal.
        `words` : mots de tour (team.turn_words). `record` : fichier où enregistrer toutes les notifications de Dofus
        relevées (None : aucun, et aucun relevé hors de l'attente d'un tour). `idle` : secondes entre deux relevés
        quand le relevé est coupé (pour l'enregistrement)."""
        self.names = list(names)
        self.words = [w for w in words if _fold(w).strip()]
        self.interval = interval
        self.idle = idle
        self.app = app
        self.clock = clock
        self.verbose = verbose
        self.recording = Recording(record) if record is not None else None
        self.active = False  # fil d'écoute lancé, accès accordé
        self._reader = reader
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._error: tuple[str, bool] | None = None  # (raison, paquets à installer) : écouteur désactivé
        self._seen: set[int] = set()  # Id déjà traités (jugés et enregistrés une seule fois)
        self._pending: list[tuple[float, str]] = []  # (heure de création, personnage) pas encore pris
        self._held: list[tuple[Toast, str]] = []  # (notification, personnage) lues relevé coupé, à juger (HELD)
        self._since = clock()  # notifications plus anciennes ignorées : avant le démarrage, puis avant le combat
        self._turn_ended: dict[str, float] = {}  # fin du dernier tour de chaque personnage, dans ce combat
        self._read_failures = 0
        self._shown = False  # première notification de tour de l'équipe déjà montrée au journal

    @property
    def enabled_for_polling(self) -> bool:
        return self._enabled.is_set()

    @enabled_for_polling.setter
    def enabled_for_polling(self, value: bool) -> None:
        if value:
            self._enabled.set()
        else:
            self._enabled.clear()

    # Démarrage et arrêt

    def start(self, wait: float = START_WAIT) -> bool:
        """Lance le fil d'écoute (relevé coupé tant que `enabled_for_polling` est faux : lent, pour l'enregistrement
        seulement). False, avec un message qui dit quoi faire, si les notifications ne peuvent pas être lues : le bot
        suit alors la timeline seule."""
        if self._reader is None:
            missing = missing_packages()
            if missing:
                return self._disabled(f"paquet(s) absent(s) : {', '.join(missing)}", packages=True)
            check_windows_settings(self.app)
        self._thread = threading.Thread(target=self._run, name="notifications", daemon=True)
        self._thread.start()
        if not self._ready.wait(wait):
            self._error = (f"pas de réponse de Windows en {wait:.0f} s", False)
        if self._error is not None:
            self.stop()
            return self._disabled(*self._error)
        self.active = True
        log.info("Notifications de Windows : relevées pendant l'attente des tours ; signal : un personnage de l'équipe "
                 "nommé et un mot de tour (%s) ; la fenêtre du personnage signalé est regardée d'abord, le timer de son "
                 "tour reste juge", ", ".join(f"« {w} »" for w in self.words))
        if self.recording is not None:
            log.info("Toutes les notifications de Dofus enregistrées dans %s", self.recording.path)
        return True

    def _disabled(self, reason: str, packages: bool) -> bool:
        """LE message de désactivation : pourquoi, et quoi faire."""
        todo = f"Pour les lire : {PIP} ; puis {WINDOWS_SETUP}" if packages else f"À régler : {WINDOWS_SETUP}"
        log.warning("Notifications de Windows illisibles (%s) : je suis la timeline comme d'habitude. %s", reason, todo)
        return False

    def stop(self) -> None:
        self._stop.set()
        self.enabled_for_polling = False
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2 * self.idle + API_TIMEOUT)
        self.active = False

    def _run(self) -> None:
        try:
            reader = (self._reader or (lambda: WindowsNotifications(self.app)))()
        except ImportError as error:
            self._error = (f"chargement de WinRT impossible : {error}", True)
            self._ready.set()
            return
        except Exception as error:  # noqa: BLE001 — API absente de cette version de Windows, etc.
            self._error = (f"API des notifications indisponible : {error}", False)
            self._ready.set()
            return
        try:
            try:
                refused = reader.access()
            except Exception as error:  # noqa: BLE001
                self._error = (f"accès aux notifications impossible : {error}", False)
                return
            if refused:
                self._error = (f"accès refusé par Windows, statut {refused}", False)
                return
            self._ready.set()
            while not self._stop.is_set():
                active = self.enabled_for_polling
                if active or self.recording is not None:
                    self.poll(reader)
                if active:
                    self._stop.wait(self.interval)
                else:
                    self._enabled.wait(self.idle)  # relevé coupé (hors combat, tour d'un personnage) : lent
        finally:
            self._ready.set()
            close = getattr(reader, "close", None)
            if close is not None:
                close()

    # Relevé (fil d'écoute)

    def poll(self, reader) -> list[str]:
        """Un relevé ; renvoie les personnages nouvellement signalés. Relevé coupé (`enabled_for_polling` faux) : pour
        l'enregistrement seulement (rien à faire sans fichier), et aucun signal."""
        active = self.enabled_for_polling
        if not active and self.recording is None:
            return []
        try:
            toasts = reader.read()
        except Exception:  # noqa: BLE001 — un relevé raté ne doit pas arrêter l'écoute
            self._read_failures += 1
            if self._read_failures == 1:
                log.warning("Lecture des notifications de Windows en échec (je réessaie à chaque relevé)",
                            exc_info=True)
            return []
        return self.feed(toasts, active)

    def feed(self, toasts: Iterable[Toast], active: bool = True) -> list[str]:
        """Juge et enregistre les nouvelles notifications de Dofus ; renvoie les personnages signalés. `active` : lues
        pendant un relevé actif ; sinon aucun signal, celles qui en seraient un attendent le relevé actif suivant."""
        found, entries = [], []
        now = self.clock()
        with self._lock:
            if active:
                found += self._judge_held()
            for toast in toasts:
                if toast.app.casefold() != self.app.casefold() or toast.id in self._seen:
                    continue  # autre application (ni gardée ni enregistrée), ou déjà vue
                self._seen.add(toast.id)
                names = names_in(toast.texts, self.names)
                name = names[0] if len(names) == 1 else None
                word = turn_word_in(toast.texts, self.words, name)
                decision = self._decide(toast, names, word)
                if decision == SIGNAL and not active:
                    decision = HELD
                    self._held.append((toast, name))
                self._show(toast, name, decision, now)
                entries.append({"lue": _when(now), "creee": _when(toast.created), "id": toast.id,
                                "application": toast.app, "textes": list(toast.texts), "personnage": name,
                                "noms": names, "mot_de_tour": word, "decision": decision, "releve_actif": active})
                if decision == SIGNAL:
                    self._pending.append((toast.created, name))
                    found.append(name)
        if self.recording is not None:
            for entry in entries:
                self.recording.write(entry)
        return found

    def _decide(self, toast: Toast, names: list[str], word: str | None) -> str:
        """Décision pour une nouvelle notification de Dofus, comme lue pendant un relevé actif (verrou tenu)."""
        if not names:
            return NO_NAME
        if len(names) > 1:
            return MANY_NAMES
        if word is None:
            return NO_TURN_WORD
        return self._too_old(toast, names[0]) or SIGNAL

    def _too_old(self, toast: Toast, name: str) -> str | None:
        """OLD ou BEFORE_LAST_TURN si la notification de tour de `name` ne compte plus ; None si elle compte."""
        if toast.created < self._since:
            return OLD
        if toast.created < self._turn_ended.get(name, 0.0):
            return BEFORE_LAST_TURN
        return None

    def _judge_held(self) -> list[str]:
        """Les notifications lues relevé coupé qui seraient un signal, jugées maintenant, pendant un relevé actif, comme
        si elles venaient d'être lues (verrou tenu) ; renvoie les personnages signalés."""
        found = []
        held, self._held = self._held, []
        for toast, name in held:
            decision = self._too_old(toast, name) or SIGNAL
            log.log(logging.INFO if self.verbose else logging.DEBUG,
                    "Notification de Dofus lue hors relevé : « %s » -> %s : %s", " | ".join(toast.texts), name,
                    decision if decision == SIGNAL else f"{decision}, ignorée")
            if decision == SIGNAL:
                self._pending.append((toast.created, name))
                found.append(name)
        return found

    def _show(self, toast: Toast, name: str | None, decision: str, now: float) -> None:
        """Au journal : celles qui ne sont pas un tour de l'équipe (leur texte dit ce que le jeu envoie d'autre), la
        première de tour (pour connaître son texte), et toutes en mode diagnostic (`verbose`) ; les autres en détail
        seulement."""
        ignored = decision in (NO_NAME, MANY_NAMES, NO_TURN_WORD)
        level = logging.INFO if ignored or self.verbose or not self._shown else logging.DEBUG
        if not ignored:
            self._shown = True
        if decision == SIGNAL:
            outcome = f"{name} : signal"
        elif decision == HELD:
            outcome = f"{name} : {HELD}, jugée au relevé actif suivant"
        else:
            outcome = f"{name + ', ' if name else ''}{decision} : ignorée"
        log.log(level, "Notification de Dofus : « %s » -> %s (lue %.2f s après sa création)", " | ".join(toast.texts),
                outcome, now - toast.created if toast.created else float("nan"))

    # Côté TeamBot

    def new_fight(self) -> None:
        """Début des tours d'un combat : les notifications d'avant (hors combat : invitations, échanges...) ignorées."""
        with self._lock:
            self._since = self.clock() - FIGHT_START_MARGIN
            self._turn_ended.clear()
            self._pending.clear()

    def playing(self, name: str) -> None:
        """Tour de `name` : relevé coupé, signaux en attente oubliés (le sien, ou d'anciens)."""
        self.enabled_for_polling = False
        with self._lock:
            self._pending.clear()

    def played(self, name: str) -> None:
        """Fin du tour de `name` : ses notifications d'avant (celle de ce tour, lue en retard) ne comptent plus."""
        now = self.clock()
        with self._lock:
            self._turn_ended[name] = now
            self._pending = [p for p in self._pending if p[1] != name or p[0] >= now]

    def take(self, names: Iterable[str]) -> str | None:
        """Le dernier personnage signalé parmi `names` (ceux du combat), ou None. Les signaux en attente sont
        consommés : un indice ne sert qu'une fois. Relevé actif : celles lues relevé coupé sont jugées tout de suite,
        sans attendre le relevé suivant."""
        wanted = set(names)
        with self._lock:
            if self.enabled_for_polling:
                self._judge_held()
            pending, self._pending = self._pending, []
        mine = [p for p in pending if p[1] in wanted]
        return max(mine)[1] if mine else None


def _diagnose(argv: list[str]) -> int:
    """À LANCER SOI-MÊME, le jeu ouvert (le bot ne le fait jamais) : relève les notifications sans arrêt et affiche
    chaque notification de Dofus, le personnage reconnu parmi les noms donnés, la décision (mots de tour :
    team.turn_words de config/settings.yaml) et le délai de lecture ; les enregistre toutes dans
    logs/notifications_diagnostic_<date_heure>.jsonl. N'agit sur rien : ni capture, ni souris, ni clavier, ni
    fenêtre."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    names = [a for a in argv if not a.startswith("-")]
    try:
        words = turn_words(load_settings())
    except ValueError as error:
        log.error("%s", error)
        return 1
    folder = ROOT / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / f"notifications_diagnostic_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    signals = TurnSignals(names, verbose=True, words=words, record=record)
    signals.enabled_for_polling = True  # relevé actif dès le premier relevé
    if not signals.start():
        return 1
    log.info("J'écoute les notifications de Dofus (Ctrl+C pour arrêter) ; personnages : %s",
             ", ".join(names) or "aucun")
    try:
        while True:
            time.sleep(0.5)
            signals.take(names)
    except KeyboardInterrupt:
        pass
    finally:
        signals.stop()
    return 0


if __name__ == "__main__":
    sys.exit(_diagnose(sys.argv[1:]))
