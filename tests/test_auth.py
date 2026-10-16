"""
Тесты API авторизации:
  GET  /api/auth/csrf/
  POST /api/auth/register/
  POST /api/auth/login/
  POST /api/auth/logout/
  GET  /api/auth/me/
  PATCH /api/auth/me/
  PATCH /api/auth/password/
"""

import pytest
from django.contrib.auth import get_user_model

from tests.conftest import PASSWORD, get_csrf

User = get_user_model()

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"
ME_URL = "/api/auth/me/"
PASSWORD_URL = "/api/auth/password/"
CSRF_URL = "/api/auth/csrf/"


# ─────────────────────────────── /api/auth/csrf/
@pytest.mark.django_db
class TestCsrf:
    def test_csrf_returns_token(self, api_client):
        resp = api_client.get(CSRF_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert "csrf" in data
        assert len(data["csrf"]) > 10  # токен не пустой

    def test_csrf_sets_cookie(self, api_client):
        api_client.get(CSRF_URL)
        assert "uzum_csrf" in api_client.cookies


# ─────────────────────────────── /api/auth/register/
@pytest.mark.django_db
class TestRegister:
    def test_register_success(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            REGISTER_URL,
            {
                "email": "newuser@test.uz",
                "password": PASSWORD,
                "password2": PASSWORD,
                "first_name": "Новый",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 201
        assert User.objects.filter(email="newuser@test.uz").exists()

    def test_register_user_fields_in_response(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            REGISTER_URL,
            {
                "email": "fields@test.uz",
                "password": PASSWORD,
                "password2": PASSWORD,
                "first_name": "Полей",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        data = resp.json()
        assert "email" in data
        assert "first_name" in data
        assert "password" not in data  # пароль не должен возвращаться

    def test_register_duplicate_email_400(self, api_client):
        User.objects.create_user(email="dup@test.uz", password=PASSWORD, first_name="Дублик")
        csrf = get_csrf(api_client)
        resp = api_client.post(
            REGISTER_URL,
            {
                "email": "dup@test.uz",
                "password": PASSWORD,
                "password2": PASSWORD,
                "first_name": "Второй",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400

    def test_register_password_mismatch_400(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            REGISTER_URL,
            {
                "email": "mismatch@test.uz",
                "password": PASSWORD,
                "password2": "WrongPass999",
                "first_name": "Несовпад",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400

    def test_register_invalid_email_400(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            REGISTER_URL,
            {
                "email": "not-an-email",
                "password": PASSWORD,
                "password2": PASSWORD,
                "first_name": "Неправильный",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400
        assert "fields" in resp.json()

    def test_register_without_first_name_400(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            REGISTER_URL,
            {
                "email": "noname@test.uz",
                "password": PASSWORD,
                "password2": PASSWORD,
                "first_name": "",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 400


# ─────────────────────────────── /api/auth/login/
@pytest.mark.django_db
class TestLogin:
    def setup_method(self):
        """Создаём пользователя перед каждым тестом."""
        if not User.objects.filter(email="login@test.uz").exists():
            User.objects.create_user(
                email="login@test.uz",
                password=PASSWORD,
                first_name="Логин",
            )

    def test_login_success(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            LOGIN_URL,
            {"email": "login@test.uz", "password": PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 200

    def test_login_wrong_password_401(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            LOGIN_URL,
            {"email": "login@test.uz", "password": "WrongPass999"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 401

    def test_login_unknown_email_401(self, api_client):
        csrf = get_csrf(api_client)
        resp = api_client.post(
            LOGIN_URL,
            {"email": "nobody@test.uz", "password": PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 401

    def test_login_sets_session_cookie(self, api_client):
        csrf = get_csrf(api_client)
        api_client.post(
            LOGIN_URL,
            {"email": "login@test.uz", "password": PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert "uzum_sessionid" in api_client.cookies

    def test_login_case_insensitive_email(self, api_client):
        """Email логина должен быть нечувствителен к регистру."""
        csrf = get_csrf(api_client)
        resp = api_client.post(
            LOGIN_URL,
            {"email": "Login@Test.UZ", "password": PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 200


# ─────────────────────────────── /api/auth/me/
@pytest.mark.django_db
class TestMe:
    def test_me_anonymous_401(self, api_client):
        resp = api_client.get(ME_URL)
        assert resp.status_code == 401

    def test_me_returns_user_data(self, auth_client):
        resp = auth_client.get(ME_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert "email" in data
        assert "first_name" in data
        assert data["email"] == "buyer@test.uz"

    def test_me_patch_first_name(self, auth_client):
        csrf = get_csrf(auth_client)
        resp = auth_client.patch(
            ME_URL,
            {"first_name": "Обновлённый"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        assert resp.status_code == 200
        assert resp.json()["first_name"] == "Обновлённый"


# ─────────────────────────────── /api/auth/logout/
@pytest.mark.django_db
class TestLogout:
    def test_logout_anonymous_200(self, api_client):
        """Logout идемпотентен — работает и без авторизации (возвращает 200)."""
        csrf = get_csrf(api_client)
        resp = api_client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=csrf)
        assert resp.status_code == 200

    def test_logout_success(self, auth_client):
        csrf = get_csrf(auth_client)
        resp = auth_client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=csrf)
        assert resp.status_code == 200
        # После logout — me должно вернуть 401
        assert auth_client.get(ME_URL).status_code == 401
