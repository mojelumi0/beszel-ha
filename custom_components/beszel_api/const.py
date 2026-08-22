import logging

DOMAIN = "beszel_api"
CONF_URL = "url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_UPDATE_INTERVAL = "update_interval"
LOGGER = logging.getLogger(__package__)

# Curated S.M.A.R.T. attributes exposed as extra sensor attributes on the
# S.M.A.R.T. binary sensor. Maps the raw smartctl attribute name (as reported
# by Beszel's smart_devices API) to the HA attribute key. Deliberately
# prefixed with "smart_" and excludes anything already covered by the
# existing top-level fields (power_on_hours, power_cycles, temperature) to
# avoid duplicate/colliding attribute names.
SMART_CURATED_ATTRIBUTES = {
    "Reallocated_Sector_Ct": "smart_reallocated_sectors",
    "Reallocated_Event_Count": "smart_reallocated_events",
    "Current_Pending_Sector": "smart_pending_sectors",
    "Offline_Uncorrectable": "smart_uncorrectable_sectors",
    "UDMA_CRC_Error_Count": "smart_crc_errors",
    "Wear_Leveling_Count": "smart_wear_level",
    "Media_Wearout_Indicator": "smart_wear_level",
    "SSD_Life_Left": "smart_life_left_pct",
    "Percentage_Used": "smart_life_used_pct",
    "Program_Fail_Count": "smart_program_fail_count",
    "Erase_Fail_Count": "smart_erase_fail_count",
    "Airflow_Temperature_Cel": "smart_airflow_temperature",
}
