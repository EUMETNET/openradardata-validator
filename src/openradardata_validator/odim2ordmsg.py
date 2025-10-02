import copy
import io
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy
import pandas as pd

from openradardata_validator.radar_cf import radar_cf

current_filedir = Path(__file__).parent.resolve()

schema_dir = current_filedir / "schemas"
radardb_dir = current_filedir / "stations"

DEFAULT_WIGOS = "0-0-0-"
DEFAULT_WMO = "0-20000-0-"
test_schema_path = schema_dir / "odim_to_e_soh_message.json"

# radars_format = {"WMO Code": "Int32" }
radars_format = {"WIGOS Station Identifier": "str"}

default_data_link = {
    "href": "",
    "length": 0,
    "rel": "items",
    "title": "Link to data",
    "type": "application/x-odim",
}


def init_radars(fname: Path = radardb_dir / "OPERA_RADARS.csv") -> pd.DataFrame:
    # print("Init OPERA Radar database")
    ret = pd.read_csv(fname, header=0, keep_default_na=False, dtype=radars_format)
    return ret


def get_attr(field: h5py.Group, key: str) -> Any:
    ret = None
    if key in field.attrs:
        ret = field.attrs[key]
        # Array type workaround: return the first element if len == 1
        if isinstance(ret, numpy.ndarray):
            if len(ret) == 1:
                ret = ret[0]
    return ret


def get_attr_str(field: h5py.Group, key: str) -> str | None:
    ret = get_attr(field, key)
    if ret is None:
        return ret
    if isinstance(ret, str):
        return ret
    if isinstance(ret, numpy.number):
        return str(ret)
    return str(ret.decode("utf-8"))


def find_source_type(source: str, sid: str) -> str:
    ret = ""
    source_list = source.split(",")
    for source_element in source_list:
        s = source_element.split(":")
        if s[0] == sid:
            ret = s[1]
            break
    return ret


def odim_datetime(odate: bytes, otime: bytes) -> datetime:
    if isinstance(odate, bytes):
        odate = odate.decode("utf-8")
    if isinstance(otime, bytes):
        otime = otime.decode("utf-8")
    dstr = odate + otime
    dt = datetime.strptime(dstr, "%Y%m%d%H%M%S")
    return dt


def set_meta(
    m_dest: dict[str, str | int | float],
    m_src: str,
    m_attrs: list[str],
    fmt: str = "str",
) -> None:
    for meta in m_attrs:
        meta_val = get_attr_str(m_src, meta)
        if meta_val is not None and len(meta_val):
            match fmt:
                case "str":
                    m_dest[meta] = str(meta_val)
                case "int":
                    m_dest[meta] = int(meta_val)
                case "float":
                    m_dest[meta] = float(meta_val)


def parse_odim_source(odim: h5py.File, def_msg: dict[str, Any]) -> None:
    source = get_attr_str(odim["what"], "source")
    if source is None:
        raise ValueError("ODIM file source attribute missing")
    wigos = find_source_type(source, "WIGOS")
    wmo = find_source_type(source, "WMO")
    nod = find_source_type(source, "NOD")
    org = find_source_type(source, "ORG")
    station = find_source_type(source, "PLC")

    if wigos:
        def_msg["properties"]["platform"] = wigos
    else:
        if wmo:
            def_msg["properties"]["platform"] = DEFAULT_WMO + wmo.zfill(5)
        else:
            if nod:
                def_msg["properties"]["platform"] = DEFAULT_WIGOS + nod
            else:
                if org == "247":
                    def_msg["properties"]["platform"] = "0-20010-0-" + "OPERA"
                    def_msg["properties"]["platform_name"] = "OPERA"

    if nod:
        if station:
            def_msg["properties"]["platform_name"] = "[" + nod + "]" + " " + station
        else:
            def_msg["properties"]["platform_name"] = "[" + nod + "]"
    else:
        if org == "247":
            def_msg["properties"]["period_int"] = 300
            def_msg["properties"]["period"] = "PT300S"


def parse_odim_object(odim: h5py.File, def_msg: dict[str, Any]) -> None:
    obj = get_attr_str(odim["what"], "object")
    form_version = get_attr_str(odim["what"], "version")
    def_msg["properties"]["format"] = "ODIM"
    def_msg["properties"]["radar_meta"]["format_version"] = form_version
    def_msg["properties"]["radar_meta"]["object"] = str(obj)

    if obj == "COMP":
        def_msg["geometry"] = {}
        # def_msg["geometry"]["type"] = "Polygon"
        def_msg["geometry"]["type"] = "Point"
        coords = {}
        coords["lon"] = 0.0
        coords["lat"] = 0.0
        # Set "Central coordinates"
        for corner in ["LL", "LR", "UL", "UR"]:
            for geo_coord in ["lat", "lon"]:
                meta = corner + "_" + geo_coord
                meta_val = get_attr(odim["where"], meta)
                coords[geo_coord] += meta_val
                def_msg["properties"]["radar_meta"][meta] = str(meta_val)

        coords["lat"] /= 4
        coords["lon"] /= 4
        def_msg["properties"]["hamsl"] = 0.0
        # coords["hei"] = 0.0
        def_msg["geometry"]["coordinates"] = coords
        set_meta(def_msg["properties"]["radar_meta"], odim["where"], ["projdef"], "str")
        set_meta(
            def_msg["properties"]["radar_meta"],
            odim["where"],
            ["xsize", "ysize"],
            "int",
        )
        set_meta(
            def_msg["properties"]["radar_meta"],
            odim["where"],
            ["xscale", "yscale"],
            "float",
        )
    else:
        if obj in ["PVOL", "SCAN"]:
            def_msg["properties"]["period_int"] = 300
            def_msg["properties"]["period"] = "PT300S"
            def_msg["geometry"] = {}
            def_msg["geometry"]["type"] = "Point"
            coords = {}
            coords["lat"] = get_attr(odim["where"], "lat")
            coords["lon"] = get_attr(odim["where"], "lon")
            # coords["hei"] = get_attr(odim["where"], "height")
            def_msg["geometry"]["coordinates"] = coords
            def_msg["properties"]["hamsl"] = get_attr(odim["where"], "height")

            # what attibutes => radar_meta
            if "how" in odim:
                add_attrs = [
                    "wavelength",
                    "frequency",
                    "beamwidth",
                    "beamwH",
                    "beamwV",
                    "hiprf",
                    "midprf",
                    "lowprf",
                    "antspeed",
                    "pulsewidth",
                ]
                set_meta(
                    def_msg["properties"]["radar_meta"], odim["how"], add_attrs, "float"
                )
                if "beamwidth" in def_msg["properties"]["radar_meta"]:
                    bw = def_msg["properties"]["radar_meta"]["beamwidth"]
                    def_msg["properties"]["radar_meta"]["beamwH"] = bw
                    del def_msg["properties"]["radar_meta"]["beamwidth"]
                if "wavelength" in def_msg["properties"]["radar_meta"]:
                    wl = float(def_msg["properties"]["radar_meta"]["wavelength"])
                    freq = 299792485 / (wl / 100.0)
                    def_msg["properties"]["radar_meta"]["frequency"] = str(freq)
                    del def_msg["properties"]["radar_meta"]["wavelength"]


def parse_odim_dataset_where(
    odim: h5py.File, dataset_msg: dict[str, Any], dataset_key: str, level: float
) -> float:
    obj = get_attr_str(odim["what"], "object")
    if dataset_key + "/where" in odim:
        elangle = get_attr(odim[dataset_key + "/where"], "elangle")
        if elangle is not None:
            dataset_msg["properties"]["radar_meta"]["elangle"] = float(elangle)

    if obj in ["PVOL", "SCAN"]:
        if elangle is not None:
            level = round(elangle, 2)

        # what attibutes => radar_meta int
        for meta in ["nbins", "nrays", "a1gate"]:
            meta_val = get_attr(odim[dataset_key + "/where"], meta)
            if meta_val is not None:
                dataset_msg["properties"]["radar_meta"][meta] = int(meta_val)
        # what attibutes => radar_meta float
        for meta in ["rstart", "rscale"]:
            meta_val = get_attr(odim[dataset_key + "/where"], meta)
            if meta_val is not None:
                dataset_msg["properties"]["radar_meta"][meta] = float(meta_val)
    return level


def parse_odim_dataset_what(
    odim: h5py.File, dataset_msg: dict[str, Any], dataset_key: str, level: float
) -> float:
    # Use dataset startdate, starttime
    st = odim_datetime(
        odim[dataset_key + "/what"].attrs["startdate"],
        odim[dataset_key + "/what"].attrs["starttime"],
    )
    et = odim_datetime(
        odim[dataset_key + "/what"].attrs["enddate"],
        odim[dataset_key + "/what"].attrs["endtime"],
    )
    td = et - st
    period_int = int(td.total_seconds())
    if "start_datetime" in dataset_msg["properties"] and "end_datetime" in dataset_msg["properties"]:
        # If start_datetime and end_datetime in schema, use them
        dataset_msg["properties"]["start_datetime"] = st.isoformat() + "Z"
        dataset_msg["properties"]["end_datetime"] = et.isoformat() + "Z"
        # remove period and datetime if present
        if "period_int" in dataset_msg["properties"]:
            del dataset_msg["properties"]["period_int"]
        if "period" in dataset_msg["properties"]:
            del dataset_msg["properties"]["period"]
        if "datetime" in dataset_msg["properties"]:
            del dataset_msg["properties"]["datetime"]
    else:
        # Otherwise use datetime and period
        dataset_msg["properties"]["datetime"] = st.isoformat() + "Z"
        dataset_msg["properties"]["period_int"] = period_int
        dataset_msg["properties"]["period"] = "PT" + str(period_int) + "S"

    obj = get_attr_str(odim["what"], "object")
    product = get_attr_str(odim[dataset_key + "/what"], "product")
    if product:
        dataset_msg["properties"]["radar_meta"]["product"] = product
    prodpar = get_attr(odim[dataset_key + "/what"], "prodpar")
    if prodpar is not None:
        dataset_msg["properties"]["radar_meta"]["prodpar"] = str(prodpar)

    if obj == "COMP":
        match product:
            case "CAPPI" | "PCAPPI" | "PPI" | "ETOP" | "EBASE" | "RHI":
                if prodpar is None:
                    level = 0
                else:
                    level = prodpar
            case "VIL":
                if prodpar is not None:
                    level = prodpar[1]
    return level


def parse_odim_dataset_data(
    odim: h5py.File, dataset_msg: dict[str, Any], data_key: str, ingest_list: list[str]
) -> list[dict[str, Any]]:
    msg = copy.deepcopy(dataset_msg)

    if "quantity" in odim[data_key + "/what"].attrs:
        quantity = (
            odim[data_key + "/what"]  # pylint: disable=no-member
            .attrs["quantity"]  # pylint: disable=no-member
            .decode("utf-8")  # pylint: disable=no-member
        )

        time_start = (
            dataset_msg["properties"]["datetime"]
            if "datetime" in dataset_msg["properties"]
            else dataset_msg["properties"]["start_datetime"]
        )

        current_ingest = f"{time_start}_{dataset_msg['properties']['level']}_{quantity}"
        if current_ingest not in ingest_list:
            if quantity in radar_cf:
                ingest_list.append(current_ingest)

                msg["properties"]["content"]["standard_name"] = quantity

                return [copy.deepcopy(msg)]
    return []


def parse_odim_dataset(
    odim: h5py.File, def_msg: dict[str, Any], dataset_key: str, ingest_list: list[str]
) -> list[dict[str, Any]]:
    level: float = 0
    dataset_msg = copy.deepcopy(def_msg)

    level = parse_odim_dataset_where(odim, dataset_msg, dataset_key, level)

    # Prefer dataset level what; if not present use data1 level what
    if f"{dataset_key}/what" in odim:
        level = parse_odim_dataset_what(odim, dataset_msg, dataset_key, level)
    elif f"{dataset_key}/data1/what" in odim:
        level = parse_odim_dataset_what(odim, dataset_msg, dataset_key + "/data1", level)
    else:
        raise ValueError(f"ODIM dataset what group missing in {dataset_key} and {dataset_key}/data1")
    dataset_msg["properties"]["level"] = level

    data_index = 1
    ret: list[dict[str, Any]] = []
    while f"{dataset_key}/data{data_index}/what" in odim:
        ret.extend(
            parse_odim_dataset_data(
                odim, dataset_msg, f"{dataset_key}/data{data_index}", ingest_list
            )
        )
        data_index += 1
    return ret


def odim_openradar_msgmem(
    odim_content: bytes, data_link_href: str, schema_file: Path
) -> list[dict[str, Any]]:

    with open(schema_file, mode="r", encoding="utf-8") as default_schema:
        def_msg = json.load(default_schema)

    fb = io.BytesIO(odim_content)

    odim = h5py.File(fb)

    # datetime
    dt = odim_datetime(odim["what"].attrs["date"], odim["what"].attrs["time"])
    # TODO: import api.metadata_endpoints.py -> datetime_to_iso_string
    def_msg["properties"]["datetime"] = dt.isoformat() + "Z"

    parse_odim_source(odim, def_msg)
    parse_odim_object(odim, def_msg)

    dataset_index = 1
    ingest_list: list[str] = []
    ret: list[dict[str, Any]] = []
    while f"dataset{dataset_index}/data1" in odim:
        # We will parse if data1 present
        ret.extend(
            parse_odim_dataset(odim, def_msg, f"dataset{dataset_index}", ingest_list)
        )
        dataset_index += 1

    for msg in ret:
        data_link = default_data_link
        data_link["href"] = data_link_href
        data_link["length"] = len(data_link_href)
        msg["links"].append(data_link)

    return ret


def odim2mqtt(
    odim_file_path: Path, data_link_href: str, schema_file: Path
) -> list[dict[str, Any]]:
    with open(odim_file_path, "rb") as file:
        odim_content = file.read()
    ret_str = odim_openradar_msgmem(odim_content, data_link_href, schema_file)
    return ret_str


def create_json_from_odim(
    filename: Path, data_link_href: str, schema_file: Path | None = None
) -> str:
    if schema_file is None:
        schema_file = test_schema_path

    if os.path.exists(filename):
        msg = odim2mqtt(filename, data_link_href, schema_file)
        output_text = json.dumps(msg, indent=2)
        return output_text

    raise FileNotFoundError(f"File does not exist: {filename}")
