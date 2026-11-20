import os

# Pas d'horloge en arrière-plan (ni d'appel réseau) pendant les tests.
os.environ["GARDIEN_CLOCK_ENABLED"] = "false"
