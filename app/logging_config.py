"""
Logging setup: human-readable on console, detailed in a persistent log
file so you can review what the agent did after the fact (which tool it
picked, whether it failed, etc) without scrolling back through terminal
history that's already gone.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def setup_logging(level=logging.INFO):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "agent.log")

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # full detail goes to the file

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)  # keep the terminal quiet; details live in the file

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [file_handler, console_handler]

    # Separate audit trail: every whitelisted command actually sent to the
    # VPS (executed or rejected), in its own file, independent of the
    # general app log — so "what ran against my server, when" is easy to
    # review without wading through LLM/tool-selection chatter.
    audit_path = os.path.join(LOG_DIR, "ssh_audit.log")
    audit_handler = RotatingFileHandler(audit_path, maxBytes=1_000_000, backupCount=5)
    audit_handler.setFormatter(formatter)

    audit_logger = logging.getLogger("ssh_audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.handlers = [audit_handler]
    audit_logger.propagate = False  # don't also spam it into agent.log