"""How fast an agent may look: the rate of its looks inside a sliding window."""

from hopin.config.rate import Rate
from hopin.participation.vocabulary import LOOK_WINDOW_SECONDS, LOOKS_PER_WINDOW

LOOK_RATE = Rate(LOOKS_PER_WINDOW, LOOK_WINDOW_SECONDS)
