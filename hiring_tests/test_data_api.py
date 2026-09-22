"""Execute our submitted checks against isolated SQLite, never the live MAX bot."""
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import yaml
from sqlalchemy import select

from hiring.db import Application, Job, Outbox
from test_product import client, register

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


def substitute(value, variables):
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, str):
        return VARIABLE.sub(lambda m: str(variables[m[1]]), value)
    return value


def execute(client, step, roles, variables, default_headers):
    request = substitute(step.get("request", {}), variables)
    path = step["path"]
    for key, value in request.get("path", {}).items():
        path = path.replace("{" + key + "}", str(value))
    headers = {**default_headers, **roles[step["role"]], **request.get("headers", {})}
    args = {"headers": headers, "params": request.get("query", {})}
    if "body" in request:
        args["json"] = request["body"]
    response = client.request(step["method"], path, **args)
    expected = step["expected"]
    assert response.status_code in expected["statusCodes"], (step["id"], response.status_code, response.text)
    if "contentType" in expected:
        assert response.headers["content-type"].split(";")[0] == expected["contentType"], step["id"]
    if "requiredFields" in expected or "bodySchema" in expected or step.get("extract"):
        body = response.json()
        assert all(field in body for field in expected.get("requiredFields", [])), step["id"]
        if "bodySchema" in expected:
            jsonschema.Draft202012Validator(expected["bodySchema"]).validate(body)
        for name, expression in step.get("extract", {}).items():
            assert re.fullmatch(r"\$\.[a-zA-Z_][a-zA-Z_0-9]*", expression), "Only the used $.field subset is implemented"
            variables[name] = body[expression[2:]]


def test_data_api_scenario_twice_with_cleanup(client):
    document = yaml.safe_load((ROOT / "DATA-API.yaml").read_text(encoding="utf-8"))
    roles = {"public": {}, "employer": register(client, "data-api-employer", "employer"),
             "candidate": register(client, "data-api-candidate"),
             "other_employer": register(client, "data-api-other", "employer")}
    for _ in range(2):
        variables, completed = {}, set()
        try:
            for step in document["checks"]:
                assert set(step.get("dependsOn", [])) <= completed
                execute(client, step, roles, variables, document["api"]["defaultHeaders"])
                completed.add(step["id"])
        finally:
            for step in document["cleanup"]:
                needed = set(VARIABLE.findall(json.dumps(step)))
                if needed <= variables.keys():
                    execute(client, step, roles, variables, document["api"]["defaultHeaders"])
    with client.app.state.factory() as db:
        assert all(not job.active for job in db.scalars(select(Job)))
        for app in db.scalars(select(Application)):
            assert app.status == "withdrawn" and not app.resume and not app.answers and not app.invitation
        assert list(db.scalars(select(Outbox))) == [], "Technical verification must not send MAX messages"


def test_official_data_api_validator():
    result = subprocess.run([sys.executable, str(ROOT / "tools/data_api/validate_data_api.py"),
                             str(ROOT / "DATA-API.yaml")], capture_output=True, encoding="utf-8",
                            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ВНИМАНИЕ" not in result.stdout


def test_openapi_export_matches_current_backend(client):
    assert json.loads((ROOT / "openapi.json").read_text(encoding="utf-8")) == client.app.openapi()
