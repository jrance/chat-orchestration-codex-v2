from app.config.settings import settings
from app.prompt.assembler import assemble_system_prompt


def test_prompt_assembly_order(monkeypatch) -> None:
    monkeypatch.setattr(settings, "org_preamble_text", None)
    monkeypatch.setattr(settings, "org_preamble_path", None)

    agent = {
        "systemInstructions": "Hello {tenant_id}.",
        "styleGuide": "Be brief.",
        "context": {"injectOrgPreamble": False, "vars": {"lang": "en"}},
    }

    prompt = assemble_system_prompt(agent, {"tenant_id": "t-1"})

    assert "### STYLE GUIDE" in prompt
    assert "### INSTRUCTIONS" in prompt
    assert "Hello t-1." in prompt
    assert "### CONTEXT VARS" in prompt
    assert prompt.index("### STYLE GUIDE") < prompt.index("### INSTRUCTIONS")


def test_prompt_includes_org_preamble(monkeypatch) -> None:
    monkeypatch.setattr(settings, "org_preamble_text", "Stay calm.")
    monkeypatch.setattr(settings, "org_preamble_path", None)

    agent = {
        "systemInstructions": "Respond politely.",
        "context": {"injectOrgPreamble": True},
    }

    prompt = assemble_system_prompt(agent, {})

    assert prompt.startswith("### ORGANIZATION PREAMBLE")
    assert "Stay calm." in prompt
