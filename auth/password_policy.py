import re

WEAK_PASSWORDS = {
    "Admin123!", "Password123!", "qwerty123", "12345678",
    "Qwerty123!", "password123", "Test1234!", "Welcome1!",
}

_SPECIAL = re.compile(r"[!@#$%^&*()\-_=+\[\]{};':\"\\|,.<>/?`~]")


def validate_password_policy(password: str, email: str = "") -> list:
    errors = []
    if len(password) < 8:
        errors.append("Мінімум 8 символів")
    if not re.search(r"[A-Z]", password):
        errors.append("Мінімум 1 велика літера (A–Z)")
    if not re.search(r"[a-z]", password):
        errors.append("Мінімум 1 мала літера (a–z)")
    if not re.search(r"\d", password):
        errors.append("Мінімум 1 цифра (0–9)")
    if not _SPECIAL.search(password):
        errors.append("Мінімум 1 спецсимвол (!@#$%^&* тощо)")
    if email:
        local = email.split("@")[0].lower()
        if local and local in password.lower():
            errors.append("Пароль не може містити частину email")
    if password in WEAK_PASSWORDS:
        errors.append("Занадто простий пароль — оберіть надійніший")
    return errors
