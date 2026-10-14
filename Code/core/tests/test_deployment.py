"""Static checks that the ARM template, SKU parameter files and portal form agree."""
import json
from pathlib import Path

import pytest

ARM = Path(__file__).resolve().parents[3] / "Deployment" / "ARM"
TEMPLATE = json.loads((ARM / "azuredeploy.json").read_text())
PARAM_FILES = sorted((ARM / "parameters").glob("*.parameters.json"))


@pytest.mark.parametrize("path", PARAM_FILES, ids=lambda p: p.name)
def test_parameter_files_match_template(path):
    values = json.loads(path.read_text())["parameters"]
    for name, item in values.items():
        assert name in TEMPLATE["parameters"], f"{name} is not a template parameter"
        allowed = TEMPLATE["parameters"][name].get("allowedValues")
        if allowed:
            assert item["value"] in allowed, f"{name}={item['value']} not in {allowed}"


def test_ui_definition_outputs_are_parameters():
    ui = json.loads((ARM / "createUiDefinition.json").read_text())
    for name in ui["parameters"]["outputs"]:
        assert name in TEMPLATE["parameters"]


def test_queue_names_match_functions():
    source = (ARM.parents[1] / "Code" / "functions" / "function_app.py").read_text()
    for queue in TEMPLATE["variables"]["queues"]:
        assert f'queue_name="{queue}"' in source
