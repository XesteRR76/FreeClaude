import pytest
from app.antigravity_engine import is_project_assembly_intent


def test_is_project_assembly_intent_russian():
    req1 = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Пожалуйста, собери проект на FastAPI с базой данных"}
        ],
    }
    assert is_project_assembly_intent(req1) is True

    req2 = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Нужна сборка проекта для мобильного приложения"}
        ],
    }
    assert is_project_assembly_intent(req2) is True

    req3 = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Создай проект интернет-магазина"}
        ],
    }
    assert is_project_assembly_intent(req3) is True


def test_is_project_assembly_intent_english():
    req = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Please build the project structure for my Next.js site"}
        ],
    }
    assert is_project_assembly_intent(req) is True

    req_scaffold = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Scaffold project with docker compose"}
        ],
    }
    assert is_project_assembly_intent(req_scaffold) is True


def test_is_project_assembly_intent_explicit_model():
    req = {
        "model": "antigravity",
        "messages": [
            {"role": "user", "content": "Check code syntax"}
        ],
    }
    assert is_project_assembly_intent(req) is True


def test_regular_prompt_not_build():
    req = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Какая погода в Москве?"}
        ],
    }
    assert is_project_assembly_intent(req) is False
