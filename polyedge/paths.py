from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

LOGS_DIR = ROOT_DIR / "logs"
AUDIT_DIR = LOGS_DIR / "audit"
KILLSWITCH_PATH = LOGS_DIR / "killswitch.json"
RUNTIME_CONFIG_PATH = LOGS_DIR / "runtime_config.json"
HEALTH_PATH = LOGS_DIR / "health.json"
SIM_STATE_PATH = LOGS_DIR / "simulation_state.json"
CONFIG_ENV_PATH = ROOT_DIR / "config" / ".env"
