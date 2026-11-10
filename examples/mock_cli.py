"""A stand-in 'model CLI' for testing the command provider offline:
    python run.py --input show.zip --provider command --provider-opt cmd="python3 examples/mock_cli.py"
Reads the prompt on stdin and prints a valid (non-editorial) JSON reply."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from promo_finder.providers import Mock

print(Mock().complete("", sys.stdin.read(), None))
