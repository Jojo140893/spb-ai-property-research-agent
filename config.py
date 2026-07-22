"""
Global Configuration & Environment Settings for SPB AI Property Research Agent.
Eliminates hardcoded paths and centralizes system constants.
"""

import os
from pathlib import Path

# Project Base Directory
PROJECT_ROOT = Path(__file__).parent.resolve()

# Directory Paths
DATA_DIR = PROJECT_ROOT
OUTPUT_DIR = PROJECT_ROOT / "output"
DRIVE_INPUT_DIR = PROJECT_ROOT / "drive_input"
BUILDER_CSV_PATH = PROJECT_ROOT / "Book1(Builders) List.csv"
SOP_DOCX_PATH = PROJECT_ROOT / "SPB_AI_Property_Research_Agent_SOP_Detailed for Digi.docx"
DATABASE_PATH = PROJECT_ROOT / "spb_research_audit.db"

# Ensure directories exist
OUTPUT_DIR.mkdir(exist_ok=True)
DRIVE_INPUT_DIR.mkdir(exist_ok=True)

# Kommo CRM Integration Credentials
KOMMO_SUBDOMAIN = os.getenv("KOMMO_SUBDOMAIN", "spb")
KOMMO_ACCESS_TOKEN = os.getenv("KOMMO_ACCESS_TOKEN", "")
KOMMO_DRY_RUN = os.getenv("KOMMO_DRY_RUN", "True").lower() == "true"

# Google Drive Integration Credentials
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", str(PROJECT_ROOT / "credentials.json"))

# E-Agent Default Login Credentials
E_AGENT_USERNAME = os.getenv("E_AGENT_USERNAME", "sales@e-agent.com.au")
E_AGENT_PASSWORD = os.getenv("E_AGENT_PASSWORD", "Ep123456")

# Server Configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
