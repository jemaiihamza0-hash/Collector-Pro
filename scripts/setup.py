from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    raise SystemExit(".env existe déjà ; aucun secret remplacé.")
text = (root / ".env.example").read_text()
text = text.replace("\nAPI_KEY=\n", "\nAPI_KEY=" + secrets.token_urlsafe(32) + "\n")
text = text.replace(
    "\nREADER_API_KEY=\n", "\nREADER_API_KEY=" + secrets.token_urlsafe(32) + "\n"
)
text = text.replace(
    "POSTGRES_PASSWORD=\n", "POSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n"
)
path.write_text(text)
try:
    path.chmod(0o600)
except OSError:
    pass
print(".env créé. La clé de connexion est dans API_KEY. Ne pas publier ce fichier.")
