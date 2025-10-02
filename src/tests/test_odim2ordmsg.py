import os
from pathlib import Path

import pytest

from openradardata_validator.odim2ordmsg import create_json_from_odim


current_filedir = Path(__file__).parent.resolve()


@pytest.mark.parametrize(
    ["filename"],
    [
        (file,)
        for file in os.listdir(current_filedir / "data/odim")
        if file.endswith(".h5") or file.endswith(".hdf")
    ],
)
def test_create_json_from_odim(filename: str) -> None:
    # Use schema if available with the same name as the file but with .json extension
    schema_path = current_filedir / "data/schemas" / f"{filename}.json"
    if schema_path.exists():
        schema_file = schema_path
    else:
        schema_file = None
    output_text = create_json_from_odim(
        current_filedir / "data/odim" / filename, "https://placeholder.url", schema_file=schema_file
    )

    with open(current_filedir / "data/odim" / f"{filename}.json", encoding="utf-8") as reference_file:
        assert reference_file.read() == output_text
