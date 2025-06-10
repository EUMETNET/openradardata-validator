import copy
import datetime
import io
import json
import os
import sys

import h5py
import numpy
import pandas

from src.radar_cf import radar_cf

odr_validator_dir = os.getenv("ORD_VALIDATOR_DIR", ".")
schema_dir = os.getenv("ORD_SCHEMA_DIR", odr_validator_dir + "/schemas")
radardb_dir = os.getenv("ORD_STATIONS_DIR", odr_validator_dir + "/stations")

default_wigos = "0-0-0-"
default_wmo = "0-20000-0-"
test_schema_path = schema_dir + "/odim_to_e_soh_message.json"
default_url = "https://My-default-URL"

S3_BUCKET_NAME = "My-S3-Bucket"
S3_ACCESS_KEY = ""
S3_SECRET_ACCESS_KEY = ""
S3_ENDPOINT_URL = "https://My-Data-Server.com"

radars = None
# radars_format = {"WMO Code": "Int32" }
radars_format = {"WIGOS Station Identifier": "str"}


def init_radars(fname=radardb_dir + "/OPERA_RADARS.csv"):
    # print("Init OPERA Radar database")
    ret = pandas.read_csv(fname, header=0, keep_default_na=False, dtype=radars_format)
    return ret


def get_attr(field: object, key: str):
    ret = None
    if key in field.attrs:
        ret = field.attrs[key]
        # Array type workaround: return the first element if len == 1
        if isinstance(ret, numpy.ndarray):
            if len(ret) == 1:
                ret = ret[0]
    return ret


def get_attr_str(field: object, key: str) -> str:
    ret = get_attr(field, key)
    if ret is None:
        return ret
    if isinstance(ret, str):
        return ret
    if isinstance(ret, numpy.number):
        return str(ret)
    return ret.decode("utf-8")


def find_source_type(source: str, id: str) -> str:
    ret = ""
    source_list = source.split(",")
    for source_element in source_list:
        s = source_element.split(":")
        if s[0] == id:
            ret = s[1]
            break
    return ret


def odim_datetime(odate, otime: bytes) -> datetime:
    dstr = (odate + otime).decode("utf-8")
    dt = datetime.datetime.strptime(dstr, "%Y%m%d%H%M%S")
    return dt


def set_meta(m_dest: object, m_src: str, m_attrs: list):
    for meta in m_attrs:
        meta_val = get_attr_str(m_src, meta)
        if meta_val is not None and len(meta_val):
            m_dest[meta] = meta_val


def odim_openradar_msgmem(odim_content, size, test_schema_path):
    ret = []
    s3_key_dir = ""
    s3_key_fil = ""
    s3_key_level = []
    s3_key_quan = []

    global radars
    if radars is None:
        radars = init_radars()

    with open(test_schema_path, mode="r", encoding="utf-8") as default_schema:
        def_msg = json.load(default_schema)

    # fb_up = io.BytesIO(odim_content)
    fb = io.BytesIO(odim_content)
    try:
        odim = h5py.File(fb)
    except Exception as e:
        print("ODIM IO ERROR: {0}".format(e))
        return ret

    # print("ODIM WHAT: {0}".format(odim["what"]))
    # if "where" in odim:
    #    print("ODIM WHERE: {0}".format(odim["where"]))
    # if "how" in odim:
    #    print("ODIM HOW: {0}".format(odim["how"]))

    # datetime
    dt = odim_datetime(odim["what"].attrs["date"], odim["what"].attrs["time"])
    # TODO: import api.metadata_endpoints.py -> datetime_to_iso_string
    def_msg["properties"]["datetime"] = dt.isoformat() + "Z"
    s3_key_dir += datetime.datetime.strftime(dt, "%Y/%m/%d/")
    s3_key_fil += datetime.datetime.strftime(dt, "%Y%m%dT%H%M")

    # source
    source = get_attr_str(odim["what"], "source")
    wigos = find_source_type(source, "WIGOS")
    wmo = find_source_type(source, "WMO")
    nod = find_source_type(source, "NOD")
    org = find_source_type(source, "ORG")
    station = find_source_type(source, "PLC")

    # # Old ODIM implementation, NOD is missing
    # if len(nod) == 0 and org != "247":
    #     radar_found = False
    #     for i in range(1, len(radars)):
    #         rad_wmo = radars["WMO Code"][i]
    #         rad_wigos = radars["WIGOS Station Identifier"][i]
    #         if len(rad_wmo) and int(rad_wmo) == int(wmo):
    #             nod = radars["ODIM code"][i]
    #             print("Found ODIM code: {0} [WMO: {1}]".format(nod, wmo))
    #             radar_found = True
    #         if len(rad_wigos) > 0 and rad_wigos == wigos:
    #             nod = radars["ODIM code"][i]
    #             print("Found ODIM code: {0} [WIGOS: {1}]".format(nod, wigos))
    #             radar_found = True
    #         if radar_found:
    #             break

    # Fill WIGOS, WMO, Station when missing
    # for i in range(1, len(radars)):
    #     if nod == radars["ODIM code"][i]:
    #         # print("FOUND: {0} ".format(nod))
    #         if isinstance(wmo, str) and len(wmo) == 0:
    #             wmo = radars["WMO Code"][i]
    #         if len(wigos) == 0:
    #             rad_wigos = radars["WIGOS Station Identifier"][i]
    #             if len(rad_wigos) != 0:
    #                 wigos = rad_wigos
    #         if len(station) == 0:
    #             station = radars["Location"][i]
    #         break

    if len(wigos):
        def_msg["properties"]["platform"] = wigos
    else:
        if len(wmo):
            def_msg["properties"]["platform"] = default_wmo + wmo.zfill(5)
        else:
            if len(nod):
                def_msg["properties"]["platform"] = default_wigos + nod
            else:
                if org == "247":
                    def_msg["properties"]["platform"] = "0-20010-0-" + "OPERA"
                    def_msg["properties"]["platform_name"] = "OPERA"

    if len(nod):
        if len(station):
            def_msg["properties"]["platform_name"] = "[" + nod + "]" + " " + station
        else:
            def_msg["properties"]["platform_name"] = "[" + nod + "]"

    if len(nod):
        s3_key_dir += str(nod)[:2].upper() + "/" + str(nod) + "/"
        s3_key_fil = str(nod) + "@" + s3_key_fil
    else:
        if org == "247":
            s3_key_dir += "OPERA/"
            s3_key_fil = "OPERA" + "@" + s3_key_fil
            def_msg["properties"]["period_int"] = 300
            def_msg["properties"]["period"] = "PT300S"
        else:
            s3_key_dir += "Unknown/" + def_msg["properties"]["platform"] + "/"
            s3_key_fil = "Unknown" + "@" + s3_key_fil

    object = get_attr_str(odim["what"], "object")
    form_version = get_attr_str(odim["what"], "version")
    def_msg["properties"]["radar_meta"]["format"] = "ODIM"
    def_msg["properties"]["radar_meta"]["format_version"] = form_version
    def_msg["properties"]["radar_meta"]["object"] = str(object)

    if object == "COMP":
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
        add_attrs = ["projdef", "xscale", "xsize", "yscale", "ysize"]
        set_meta(def_msg["properties"]["radar_meta"], odim["where"], add_attrs)
    else:
        if object == "PVOL" or object == "SCAN":
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
                    "lowprf",
                ]
                set_meta(def_msg["properties"]["radar_meta"], odim["how"], add_attrs)
                if "beamwidth" in def_msg["properties"]["radar_meta"]:
                    bw = def_msg["properties"]["radar_meta"]["beamwidth"]
                    def_msg["properties"]["radar_meta"]["beamwH"] = bw
                    del def_msg["properties"]["radar_meta"]["beamwidth"]
                if "wavelength" in def_msg["properties"]["radar_meta"]:
                    wl = float(def_msg["properties"]["radar_meta"]["wavelength"])
                    freq = 299792485 / (wl / 100.0)
                    def_msg["properties"]["radar_meta"]["frequency"] = str(freq)
                    del def_msg["properties"]["radar_meta"]["wavelength"]

    s3_key_dir += object + "/"
    dataset_index = 1
    dataset_key = "dataset" + str(dataset_index)
    level = 0
    ingest_list = []
    while (dataset_key + "/what") in odim:
        dataset_msg = copy.deepcopy(def_msg)

        # print("DATASET WHAT: {0}".format(odim[dataset_key + "/what"]))
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
        dataset_msg["properties"]["datetime"] = st.isoformat() + "Z"
        dataset_msg["properties"]["period_int"] = period_int
        dataset_msg["properties"]["period"] = "PT" + str(period_int) + "S"

        if dataset_key + "/where" in odim:
            # print("DATASET WHERE: {0}".format(odim[dataset_key + "/where"]))
            elangle = get_attr(odim[dataset_key + "/where"], "elangle")
            if elangle is not None:
                dataset_msg["properties"]["radar_meta"]["elangle"] = str(elangle)

        # if dataset_key + "/how" in odim:
        #     print("DATASET HOW: {0}".format(odim[dataset_key + "/how"]))

        if object == "PVOL" or object == "SCAN":
            if elangle is not None:
                level = int(round(elangle, 2) * 100)
                if elangle not in s3_key_level:
                    s3_key_level.append(elangle)

            # what attibutes => radar_meta
            for meta in ["nbins", "nrays", "rscale"]:
                meta_val = get_attr(odim[dataset_key + "/where"], meta)
                if meta_val is not None:
                    dataset_msg["properties"]["radar_meta"][meta] = str(meta_val)

        product = get_attr_str(odim[dataset_key + "/what"], "product")
        if len(product):
            dataset_msg["properties"]["radar_meta"]["product"] = product
        prodpar = float(get_attr(odim[dataset_key + "/what"], "prodpar"))
        if prodpar is not None:
            dataset_msg["properties"]["radar_meta"]["prodpar"] = str(prodpar)

        if object == "COMP":
            match product:
                case "CAPPI" | "PCAPPI" | "PPI" | "ETOP" | "EBASE" | "RHI":
                    if prodpar is None:
                        level = 0
                        if 0 not in s3_key_level:
                            s3_key_level.append(0)
                    else:
                        level = prodpar
                        if prodpar not in s3_key_level:
                            s3_key_level.append(prodpar)
                case "VIL":
                    if prodpar is not None:
                        level = prodpar[1] * 100
                        for vp in prodpar:
                            if vp not in s3_key_level:
                                s3_key_level.append(vp)
                case _:
                    if 0 not in s3_key_level:
                        s3_key_level.append(0)

        dataset_msg["properties"]["level"] = level

        data_index = 1
        data_key = "dataset" + str(dataset_index) + "/data" + str(data_index)
        while (data_key + "/what") in odim:
            # print("KEY: {0}".format(data_key))
            # print("DATA WHAT: {0}".format(odim[data_key + "/what"]))
            # if data_key + "/where" in odim:
            #    print("DATA WHERE: {0}".format(odim[dataset_key + "/where"]))
            # if data_key + "/how" in odim:
            #    print("DATA HOW: {0}".format(odim[dataset_key + "/how"]))

            msg = copy.deepcopy(dataset_msg)

            # gain = get_attr(odim[data_key + "/what"], "gain")
            # offset = get_attr(odim[data_key + "/what"], "offset")

            if "quantity" in odim[data_key + "/what"].attrs:
                quantity = odim[data_key + "/what"].attrs["quantity"].decode("utf-8")

                current_ingest = st.isoformat() + "Z_" + str(level) + "_" + quantity
                if current_ingest in ingest_list:
                    # print("Duplicate quantity: {0}, skip: {1} ".format(current_ingest, data_key))
                    pass
                else:
                    if quantity in radar_cf:
                        ingest_list.append(current_ingest)

                        msg["properties"]["content"]["standard_name"] = quantity
                        if quantity not in s3_key_quan:
                            s3_key_quan.append(str(quantity))

                        ret.append(copy.deepcopy(msg))
                    else:
                        # print("WARNING, unknown quantity: {0}, skip: {1}".format(quantity, data_key))
                        pass
            data_index += 1
            data_key = "dataset" + str(dataset_index) + "/data" + str(data_index)
        dataset_index += 1
        dataset_key = "dataset" + str(dataset_index)

    # example S3 key:
    # dkrom-20240912T0355@0.47_0.65_0.96_1.46_2.36_4.47_8.49_9.96_13.0_15.0@DBZH_LDR_PHIDP_RHOHV_TH_VRAD_WRAD_ZDR.h5
    # Where:
    # ODIM Id - Datetime @ elevations(sorted, 2 digits) @ quantities(sorted) .h5
    s3_key_start_delim = "@"
    s3_key_field_delim = "_"
    s3_key_delim = s3_key_start_delim
    s3_key_level.sort()
    for el in s3_key_level:
        s3_key_fil += s3_key_delim + str(round(float(el), 2))
        if s3_key_delim == s3_key_start_delim:
            s3_key_delim = s3_key_field_delim
    s3_key_delim = s3_key_start_delim
    s3_key_quan.sort()
    for qa in s3_key_quan:
        s3_key_fil += s3_key_delim + qa
        if s3_key_delim == s3_key_start_delim:
            s3_key_delim = s3_key_field_delim
    s3_key = s3_key_dir + s3_key_fil + ".h5"

    for msg in ret:
        data_link = S3_ENDPOINT_URL
        if len(data_link) > 1 and data_link[-1] != "/":
            data_link += "/"
        data_link += S3_BUCKET_NAME + "/" + s3_key
        msg["properties"]["content"]["value"] = data_link
        msg["properties"]["content"]["unit"] = "text"
        msg["properties"]["content"]["size"] = len(data_link)

        radar_meta = msg["properties"]["radar_meta"]
        msg["properties"]["radar_meta"] = str(radar_meta)

    # S3 upload + error check

    # Update link
    if len(default_url):
        for json_str in ret:
            json_str["links"][0]["href"] = default_url
    return ret


def build_all_json_payloads_from_odim(odim_content: object) -> list[str]:
    """
    This function creates the openradar-message-spec json schema(s) from an ODIM file.

    ### Keyword arguments:
    odim_file_path (str) -- An ODIM File Path

    Returns:
    str -- mqtt message(s)

    Raises:
    ---
    """
    ret_str = []

    msg_str_list = odim_openradar_msgmem(
        odim_content, len(odim_content), test_schema_path
    )
    for json_str in msg_str_list:
        json_odim_msg = json_str
        ret_str.append(copy.deepcopy(json_odim_msg))
    return ret_str


def odim2mqtt(odim_file_path: str = "", test_schema_path: str = "") -> list[str]:
    with open(odim_file_path, "rb") as file:
        odim_content = file.read()
    ret_str = odim_openradar_msgmem(odim_content, len(odim_content), test_schema_path)
    return ret_str


if __name__ == "__main__":
    msg = ""

    if len(sys.argv) > 1:
        first_msg = True
        for i, file_name in enumerate(sys.argv):
            if i > 0:
                if os.path.exists(file_name):
                    msg = odim2mqtt(file_name, test_schema_path)
                    print("[")
                    for m in msg:
                        if first_msg:
                            first_msg = False
                        else:
                            print(",")
                        print(json.dumps(m, indent=2))
                    print("]")
                else:
                    print("File not exists: {0}".format(file_name))
                    exit(1)

    else:
        print("Generate json meaasge from ODIM file")
        print("Usage:   python3 ./odim2ordmsg.py [schema_file] ODIM_file")

    exit(0)
