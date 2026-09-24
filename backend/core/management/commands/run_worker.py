"""Exécutant des travaux automatiques de la GED.

    python manage.py run_worker                     # boucle permanente (conteneur)
    python manage.py run_worker --groupe sauvegarde # uniquement la sauvegarde
    python manage.py run_worker --une-fois          # un passage puis sortie
    python manage.py run_worker --travail ocr       # force un travail maintenant
    python manage.py run_worker --liste             # affiche l'échéancier

Remplace les boucles shell `while true; do … || true; done` qui avalaient
toute erreur : ici chaque travail est verrouillé, tracé (`JobRun`), journalisé
et signalé aux notaires en cas d'échec.
"""
import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from core.jobs import is_due, next_run_estimate, registry, run_job
from core.models import JobRun, WorkerHeartbeat


class Command(BaseCommand):
    help = "Exécute les travaux automatiques (rappels, OCR, sauvegardes, sécurité…)."

    def add_arguments(self, parser):
        parser.add_argument("--groupe", action="append", default=[],
                            help="Ne traite que ce groupe (defaut, sauvegarde). Répétable.")
        parser.add_argument("--sauf-groupe", action="append", default=[],
                            help="Exclut ce groupe. Répétable.")
        parser.add_argument("--une-fois", action="store_true", help="Un seul passage puis sortie.")
        parser.add_argument("--travail", help="Exécute immédiatement ce travail puis sort.")
        parser.add_argument("--liste", action="store_true", help="Affiche les travaux et leur échéancier.")
        parser.add_argument("--intervalle", type=int, default=20, help="Secondes entre deux passages (défaut : 20).")
        parser.add_argument("--nom", default="", help="Nom de l'exécutant (signe de vie).")

    def handle(self, *args, **options):
        specs = registry()
        if options["liste"]:
            for spec in specs.values():
                self.stdout.write(f"{spec.name:<24} [{spec.group}] {spec.schedule_label():<28} "
                                  f"prochain ≈ {timezone.localtime(next_run_estimate(spec)):%d/%m %H:%M}  — {spec.label}")
            return
        if options["travail"]:
            if options["travail"] not in specs:
                raise CommandError(f"Travail inconnu. Disponibles : {', '.join(sorted(specs))}")
            run = run_job(options["travail"], trigger=JobRun.Trigger.COMMAND)
            if run is None:
                raise CommandError("Ce travail est déjà en cours d'exécution ailleurs.")
            self.stdout.write(f"{run.name} -> {run.status} : {run.message}")
            if run.status == JobRun.Status.FAILURE:
                raise CommandError(run.error or run.message)
            return

        groupes = set(options["groupe"])
        exclus = set(options["sauf_groupe"])
        choisis = [s for s in specs.values() if (not groupes or s.group in groupes) and s.group not in exclus]
        nom = options["nom"] or ("+".join(sorted(groupes)) if groupes else "principal")
        self._stop = False
        signal.signal(signal.SIGTERM, self._arret)
        signal.signal(signal.SIGINT, self._arret)
        self.stdout.write(self.style.SUCCESS(f"Exécutant « {nom} » : {', '.join(s.name for s in choisis)}"))
        self._attendre_migrations()
        while not self._stop:
            close_old_connections()
            self._battement(nom, groupes)
            self._passage(choisis)
            # Signal de vie vers la surveillance externe (si configurée) :
            # un seul exécutant suffit, celui qui porte le groupe par défaut.
            if not groupes or "defaut" in groupes or exclus:
                from core.surveillance import signaler
                signaler()
            if options["une_fois"]:
                break
            for _ in range(max(1, options["intervalle"])):
                if self._stop:
                    break
                time.sleep(1)
        self.stdout.write("Exécutant arrêté proprement.")

    def _arret(self, *_):
        self._stop = True

    def _attendre_migrations(self):
        """N'exécute aucun travail tant que la base n'est pas au schéma du code.

        Au premier démarrage, les exécutants partaient pendant que le conteneur
        web appliquait encore les migrations : chaque travail échouait sur
        « relation … does not exist », levait une alerte et un e-mail. Même
        chose lors d'une mise à jour qui ajoute une migration."""
        from django.db import DatabaseError, connection
        from django.db.migrations.executor import MigrationExecutor

        depuis = time.monotonic()
        prevenu = False
        while not self._stop:
            try:
                close_old_connections()
                executor = MigrationExecutor(connection)
                en_attente = executor.migration_plan(executor.loader.graph.leaf_nodes())
            except DatabaseError:
                en_attente = True  # base pas encore joignable
            if not en_attente:
                if prevenu:
                    self.stdout.write(f"Base à jour après {int(time.monotonic() - depuis)} s : démarrage des travaux.")
                return
            if not prevenu:
                self.stdout.write("Migrations en attente : les travaux démarreront une fois la base à jour.")
                prevenu = True
            time.sleep(3)

    def _battement(self, nom, groupes):
        import os
        import socket
        WorkerHeartbeat.objects.update_or_create(name=nom, defaults={
            "host": socket.gethostname()[:120], "pid": os.getpid(),
            "groups": ",".join(sorted(groupes)) or "tous", "last_seen": timezone.now()})

    def _passage(self, choisis):
        noms = {s.name for s in choisis}
        # Les demandes faites depuis l'écran passent en premier.
        for demande in JobRun.objects.filter(status=JobRun.Status.REQUESTED, name__in=noms).order_by("requested_at"):
            if self._stop:
                return
            run = run_job(demande.name, trigger=JobRun.Trigger.MANUAL, user=demande.triggered_by, run=demande)
            self._compte_rendu(run, demande.name)
        for spec in choisis:
            if self._stop:
                return
            try:
                if not is_due(spec):
                    continue
            except Exception as exc:  # noqa: BLE001 — l'échéancier ne doit pas arrêter la boucle
                self.stderr.write(f"échéancier {spec.name} : {exc}")
                continue
            self._compte_rendu(run_job(spec.name), spec.name)

    def _compte_rendu(self, run, name):
        if run is None:
            return
        style = self.style.SUCCESS if run.status == JobRun.Status.SUCCESS else self.style.ERROR
        self.stdout.write(style(f"[{timezone.localtime():%H:%M:%S}] {name} -> {run.status} {run.message}"))
