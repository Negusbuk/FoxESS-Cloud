##################################################################################################
"""
Module:   Fox ESS Cloud using Open API
Updated:  14 January 2024
By:       Tony Matthews
"""
##################################################################################################
# Code for getting and setting inverter data via the Fox ESS cloud api site, including
# getting forecast data from solcast.com.au and sending inverter data to pvoutput.org
# ALL RIGHTS ARE RESERVED © Tony Matthews 2024
##################################################################################################

version = "2.0.0"
debug_setting = 1

# constants
month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
# global plot parameters
figure_width = 9       # width of plots
legend_location = "upper right"


print(f"FoxESS-Cloud Open API version {version}")

import os.path
import json
import time
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import requests
from requests.auth import HTTPBasicAuth
import hashlib
import math
import matplotlib.pyplot as plt

fox_domain = "https://www.foxesscloud.com"
api_key = None
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
time_zone = 'Europe/London'
lang = 'en'

##################################################################################################
##################################################################################################
# Fox ESS Open API Section
##################################################################################################
##################################################################################################

def query_date(d, offset = None):
    if d is not None and len(d) < 18:
        d += ' 00:00:00'
    t = datetime.now() if d is None else datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
    if offset is not None:
        t += timedelta(days = offset)
    return {'year': t.year, 'month': t.month, 'day': t.day, 'hour': t.hour, 'minute': t.minute, 'second': t.second}

def query_time(d, time_span):
    if d is not None and len(d) < 18:
        d += ' 00:00:00'
    t = datetime.now().replace(minute=0, second=0, microsecond=0) if d is None else datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
    t_begin = round(t.timestamp())
    if time_span == 'hour':
        t_end = round(t_begin + 3600)
    else:
        t_end = round((t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return (t_begin * 1000, t_end * 1000)

# interpolate a result from a list of values
def interpolate(f, v):
    if len(v) == 0:
        return None
    if f < 0.0:
        return v[0]
    elif f >= len(v) - 1:
        return v[-1]
    i = int(f)
    x = f - i
    return v[i] * (1-x) + v[i+1] * x

# build request header with signing and throttling for queries

last_query = {}
query_delay = 1

def signed_header(path, login = 0):
    global api_key, user_agent, time_zone, lang, debug_setting
    headers = {}
    token = api_key if login == 0 else ""
    t_now = time.time()
    if 'query' in path:
        t_last = last_query.get(path)
        delta = t_now - t_last if t_last is not None else query_delay
        if delta < query_delay:
            time.sleep((query_delay - delta))
        t_now = time.time()
        last_query[path] = t_now
    timestamp = str(round(t_now * 1000))
    headers['Token'] = token
    headers['Lang'] = lang
    headers['User-Agent'] = user_agent
    headers['Timezone'] = time_zone
    headers['Timestamp'] = timestamp
    headers['Content-Type'] = 'application/json'
    if login == 0:
        headers['Signature'] = hashlib.md5(fr"{path}\r\n{headers['Token']}\r\n{headers['Timestamp']}".encode('UTF-8')).hexdigest()
    if debug_setting > 1:
        print(f"path = {path}")
        print(f"headers = {headers}")
    return headers

def signed_get(path, params = None, login = 0):
    global fox_domain
    return requests.get(url=fox_domain + path, headers=signed_header(path, login), params=params)

def signed_post(path, json = None, login = 0):
    global fox_domain
    return requests.post(url=fox_domain + path, headers=signed_header(path, login), json=json)


##################################################################################################
# get error messages
##################################################################################################

messages = None

def get_messages():
    global debug_setting, messages, user_agent
    if debug_setting > 1:
        print(f"getting messages")
    headers = {'User-Agent': user_agent, 'Content-Type': 'application/json;charset=UTF-8', 'Connection': 'keep-alive'}
    response = signed_get(path="/c/v0/errors/message", login=1)
    if response.status_code != 200:
        print(f"** get_messages() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        errno = response.json().get('errno')
        print(f"** get_messages(), no result data, {errno}")
        return None
    messages = result.get('messages')
    return messages

def errno_message(response):
    global messages, lang
    errno = f"{response.json().get('errno')}"
    msg = response.json().get('msg')
    s = f"errno = {errno}"
    if msg is not None:
        return s + f": {msg}"
    if messages is None or messages.get(lang) is None or messages[lang].get(errno) is None:
        return s
    return s + f": {messages[lang][errno]}"


##################################################################################################
# get list of variables
##################################################################################################

var_table = None
raw_vars = None

def get_vars():
    global var_table, raw_vars, debug_setting, messages, lang
    if messages is None:
        get_messages()
    if raw_vars is not None:
        return raw_vars
    if debug_setting > 1:
        print(f"getting variables")
    response = signed_get(path="/op/v0/device/variable/get")
    if response.status_code != 200:
        print(f"** get_vars() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_vars(), no result data, {errno_message(response)}")
        return None
    var_table = result
    raw_vars = []
    for v in var_table:
        k = next(iter(v))
        unit = v[k]['unit'] if v[k].get('unit') is not None else ''
        name = v[k]['name'].get(lang) if v[k].get('name') is not None else k
        raw_vars.append({'variable': k , 'unit': unit, 'name': name})
    return raw_vars

##################################################################################################
# get list of sites
##################################################################################################

site_list = None
site = None
station_id = None

def get_site(name=None):
    global site_list, site, debug_setting, station_id
    if get_vars() is None:
        return None
    if site is not None and name is None:
        return site
    if debug_setting > 1:
        print(f"getting sites")
    site = None
    station_id = None
    body = {'pageSize': 100, 'currentPage': 1 }
    response = signed_post(path="/op/v0/plant/list", json=body)
    if response.status_code != 200:
        print(f"** get_sites() got list response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_site(), no list result data, {errno_message(response)}")
        return None
    total = result.get('total')
    if total is None or total == 0 or total > 100:
        print(f"** invalid list of sites returned: {total}")
        return None
    site_list = result.get('data')
    n = None
    if len(site_list) > 1:
        if name is not None:
            for i in range(len(site_list)):
                if site_list[i]['name'][:len(name)].upper() == name.upper():
                    n = i
                    break
        if n is None:
            print(f"\nget_site(): please provide a name from the list:")
            for s in site_list:
                print(f"Name={s['name']}")
            return None
    else:
        n = 0
    station_id = site_list[n]['stationID']
    params = {'id': station_id }
    response = signed_get(path="/op/v0/plant/detail", params=params)
    if response.status_code != 200:
        print(f"** get_sites() got detail response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_site(), no detail result data, {errno_message(response)}")
        return None
    site = result
    site['stationID'] = site_list[n]['stationID']
    site['ianaTimezone'] = site_list[n]['ianaTimezone']
    return site

##################################################################################################
# get list of data loggers
##################################################################################################

logger_list = None
logger = None

def get_logger(sn=None):
    global logger_list, logger, debug_setting
    if get_vars() is None:
        return None
    if logger is not None and sn is None:
        return logger
    if debug_setting > 1:
        print(f"getting loggers")
    body = {'pageSize': 100, 'currentPage': 1}
    response = signed_post(path="/op/v0/module/list", json=body)
    if response.status_code != 200:
        print(f"** get_logger() got list response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_logger(), no list result data, {errno_message(response)}")
        return None
    total = result.get('total')
    if total is None or total == 0 or total > 100:
        print(f"** invalid list of loggers returned: {total}")
        return None
    logger_list = result.get('data')
    n = None
    if len(logger_list) > 1:
        if sn is not None:
            for i in range(len(logger_list)):
                if site_list[i]['moduleSN'][:len(sn)].upper() == sn.upper():
                    n = i
                    break
        if n is None:
            print(f"\nget_logger(): please provide a serial number from this list:")
            for l in logger_list:
                print(f"SN={l['moduleSN']}, Plant={l['plantName']}, StationID={l['stationID']}")
            return None
    else:
        n = 0
    logger = logger_list[n]
    return logger


##################################################################################################
# get list of devices and select one, using the serial number if there is more than 1
##################################################################################################

device_list = None
device = None
device_sn = None

def get_device(sn=None):
    global device_list, device, device_sn, firmware, battery, raw_vars, debug_setting, schedule
    if get_vars() is None:
        return None
    #######
    if device_sn is not None:
        device = {}
        return device_sn
    #######
    if device is not None:
        if sn is None:
            return device
        if device_sn[:len(sn)].upper() == sn.upper():
            return device
    if debug_setting > 1:
        print(f"getting device")
    if sn is None and device_sn is not None and len(device_sn) == 15:
        sn = device_sn
    # get device list
    body = {'pageSize': 100, 'currentPage': 1}
    response = signed_post(path="/op/v0/device/list", json=body)
    if response.status_code != 200:
        print(f"** get_device() list got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_device(), no list result data, {errno_message(response)}")
        return None
    total = result.get('total')
    if total is None or total == 0 or total > 100:
        print(f"** invalid list of devices returned: {total}")
        return None
    device_list = result.get('data')
    # look for the device we want in the list
    n = None
    if len(device_list) == 1 and sn is None:
        n = 0
    else:
        for i in range(len(device_list)):
            if device_list[i]['deviceSN'][:len(sn)].upper() == sn.upper():
                n = i
                break
        if n is None:
            print(f"\nget_device(): please provide a serial number from this list:")
            for d in device_list:
                print(f"SN={d['deviceSN']}, Type={d['deviceType']}")
            return None
    # load information for the device
    device_sn = device_list[n].get('deviceSN')
    params = {'sn': device_sn }
    response = signed_get(path="/op/v0/device/detail", params=params)
    if response.status_code != 200:
        print(f"** get_device() got detail response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_device(), no detail result data, {errno_message(response)}")
        return None
    device = result
    battery = None
    battery_settings = None
    schedule = None
    get_generation()
    # parse the model code to work out attributes
    model_code = device['deviceType'].upper()
    # first 2 letters / numbers e.g. H1, H3, KH
    if model_code[:2] == 'KH':
        mode_code = 'KH-' + model_code[2:]
    elif model_code[:4] == 'AIO-':
        mode_code = 'AIO' + model_code[4:]
    device['eps'] = 'E' in model_code
    parts = model_code.split('-')
    model = parts[0]
    if model not in ['KH', 'H1', 'AC1', 'H3', 'AC3', 'AIOH1', 'AIOH3']:
        print(f"** device model not recognised for deviceType: {device['deviceType']}")
        return device
    device['model'] = model
    device['phase'] = 3 if model[-1:] == '3' else 1
    for p in parts[1:]:
        if p.replace('.','').isnumeric():
            power = float(p)
            if power >= 1.0 and power < 20.0:
                device['power'] = float(p)
            break
    if device.get('power') is None:
        print(f"** device power not found for deviceType: {device['deviceType']}")
    # set max charge current
    if model in ['KH']:
        device['max_charge_current'] = 50
    elif model in ['H1', 'AC1']:
        device['max_charge_current'] = 35
    elif model in ['H3', 'AC3', 'AIOH3']:
        device['max_charge_current'] = 26
    else:
        device['max_charge_current'] = 40
    return device

##################################################################################################
# get generation info and save to device
##################################################################################################

def get_generation():
    global device_sn, device
    if get_device() is None:
        return None
    if debug_setting > 1:
        print(f"getting generation")
    params = {'sn': device_sn}
    response = signed_get(path="/op/v0/device/generation", params=params)
    if response.status_code != 200:
        print(f"** get_generation() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_generation(), no result data, {errno_message(response)}")
        return None
    device['generationToday'] = result['today']
    device['generationMonth'] = result['month']
    device['generationTotal'] = result['cumulative']
    return device

##################################################################################################
# get battery info and save to battery
##################################################################################################

battery = None
battery_settings = None
battery_capacity = None

def get_battery():
    global device_sn, battery, debug_setting, battery_capacity
    if get_device() is None:
        return None
    if debug_setting > 1:
        print(f"getting battery")
    params = {'sn': device_sn}
    body = {'sn': device_sn, 'variables': ['Soc', 'invBatVolt', 'invBatCurrent', 'invBatPower', 'batTemperature' ]}
    response = signed_post(path="/op/v0/real/query", json=body)
    if response.status_code != 200:
        print(f"** get_battery() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_battery(), no result data, {errno_message(response)}")
        return None
    return result
    battery = result['batterys'][0]
    if battery.get('current') is None or battery['current'] == 0.0:
        battery['current'] = round(battery['power'] * 1000 / battery['volt'] ,1)
    if (battery.get('residual') is None or battery['residual'] == 0.0) and battery_capacity is not None:
        battery['residual'] = round(battery_capacity * battery['soc'] * 10, 0)
    if battery_capacity is None and battery.get('residual') is not None and battery['residual'] > 0.0 and battery['soc'] > 0:
        battery_capacity = battery['residual'] / battery['soc'] / 10
    return battery

##################################################################################################
# get charge times and save to battery_settings
##################################################################################################

def get_charge():
    global token, device_sn, battery_settings, debug_setting
    if get_device() is None:
        return None
    if battery_settings is None:
        battery_settings = {}
    if debug_setting > 1:
        print(f"getting charge times")
    params = {'sn': device_sn}
    response = signed_get(path="/op/v0/device/battery/forceChargeTime/get", params=params)
    if response.status_code != 200:
        print(f"** get_charge() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_charge(), no result data, {errno_message(response)}")
        return None
    times = result.get('times')
    if times is None:
        print(f"** get_charge(), no times data, {errno_message(response)}")
        return None
    battery_settings['times'] = times
    return battery_settings


##################################################################################################
# set charge times from battery_settings or parameters
##################################################################################################

# helper to format time period structure
def time_period(t):
    result = f"{t['startTime']['hour']:02d}:{t['startTime']['minute']:02d} - {t['endTime']['hour']:02d}:{t['endTime']['minute']:02d}"
    if t['startTime']['hour'] != t['endTime']['hour'] or t['startTime']['minute'] != t['endTime']['minute']:
        result += f" Charge from grid" if t['enableGrid'] else f" Force Charge"
    return result

def set_charge(ch1 = None, st1 = None, en1 = None, ch2 = None, st2 = None, en2 = None, adjust = 0, force = 0):
    global token, device_sn, battery_settings, debug_setting
    if get_device() is None:
        return None
    if battery_settings is None:
        battery_settings = {}
    if battery_settings.get('times') is None:
        battery_settings['times'] = []
        battery_settings['times'].append({'tip': '', 'enable': False, 'startTime': {'hour': 0, 'minute': 0}, 'endTime': {'hour': 0, 'minute': 0}})
        battery_settings['times'].append({'tip': '', 'enable': False, 'startTime': {'hour': 0, 'minute': 0}, 'endTime': {'hour': 0, 'minute': 0}})
    if get_schedule().get('enable'):
        if force == 0:
            print(f"** set_charge(): cannot set charge when a schedule is enabled")
            return None
        set_schedule(enable=0)
    # configure time period 1
    if st1 is not None:
        if st1 == en1:
            st1 = 0
            en1 = 0
            ch1 = False
        else:
            st1 = round_time(time_hours(st1) + adjust)
            en1 = round_time(time_hours(en1) + adjust)
        battery_settings['times'][0]['enable'] = True if ch1 == True or ch1 == 1 else False
        battery_settings['times'][0]['startTime']['hour'] = int(st1)
        battery_settings['times'][0]['startTime']['minute'] = int(60 * (st1 - int(st1)) + 0.5)
        battery_settings['times'][0]['endTime']['hour'] = int(en1)
        battery_settings['times'][0]['endTime']['minute'] = int(60 * (en1 - int(en1)) + 0.5)
    # configure time period 2
    if st2 is not None:
        if st2 == en2:
            st2 = 0
            en2 = 0
            ch2 = False
        else:
            st2 = round_time(time_hours(st2) + adjust)
            en2 = round_time(time_hours(en2) + adjust)
        battery_settings['times'][1]['enable'] = True if ch2 == True or ch2 == 1 else False
        battery_settings['times'][1]['startTime']['hour'] = int(st2)
        battery_settings['times'][1]['startTime']['minute'] = int(60 * (st2 - int(st2)) + 0.5)
        battery_settings['times'][1]['endTime']['hour'] = int(en2)
        battery_settings['times'][1]['endTime']['minute'] = int(60 * (en2 - int(en2)) + 0.5)
    if debug_setting > 1:
        print(f"set_charge(): {battery_settings}")
        return None
    if debug_setting > 0:
        print(f"\nSetting time periods:")
        print(f"   Time Period 1 = {time_period(battery_settings['times'][0])}")
        print(f"   Time Period 2 = {time_period(battery_settings['times'][1])}")
    # set charge times
    data = {'sn': device_sn, 'times': battery_settings.get('times')}
    response = signed_post(path="/op/v0/device/battery/forceChargeTime/set", data=json.dumps(data))
    if response.status_code != 200:
        print(f"** set_charge() got response code {response.status_code}: {response.reason}")
        return None
    errno = response.json().get('errno')
    if errno != 0:
        if errno == 44096:
            print(f"** set_charge(), cannot update settings when schedule is active")
        else:
            print(f"** set_charge(), {errno_message(response)}")
        return None
    elif debug_setting > 1:
        print(f"success") 
    return battery_settings

##################################################################################################
# get min soc settings and save in battery_settings
##################################################################################################

def get_min():
    global token, device_sn, battery_settings, debug_setting
    if get_device() is None:
        return None
    if battery_settings is None:
        battery_settings = {}
    if debug_setting > 1:
        print(f"getting min soc")
    params = {'sn': device_sn}
    response = signed_get(path="/op/v0/device/battery/soc/get", params=params)
    if response.status_code != 200:
        print(f"** get_min() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_min(), no result data, {errno_message(response)}")
        return None
    battery_settings['minSoc'] = result.get('minSoc')
    battery_settings['minSocOnGrid'] = result.get('minSocOnGrid')
    return battery_settings

##################################################################################################
# set min soc from battery_settings or parameters
##################################################################################################

def set_min(minSocOnGrid = None, minSoc = None, force = 0):
    global token, device_sn, bat_settings, debug_setting, default_min_soc
    if get_device() is None:
        return None
    if get_schedule().get('enable'):
        if force == 0:
            print(f"** set_min(): cannot set min SoC mode when a schedule is enabled")
            return None
        set_schedule(enable=0)
    data = {'sn': device_sn}
    if battery_settings is None:
        battery_settings = {}
    if minSocOnGrid is not None:
        data['minSocOnGrid'] = minSocOnGrid
        battery_settings['minSocOnGrid'] = minSocOnGrid
    if minSoc is not None:
        data['minSoc'] = minSoc
        battery_settings['minSoc'] = minSoc
    if debug_setting > 1:
        print(f"set_min(): {battery_settings}")
        return None
    if debug_setting > 0:
        print(f"\nSetting minSoc = {battery_settings.get('minSoc')}, minSocOnGrid = {battery_settings.get('minSocOnGrid')}")
    response = signed_post(path="/op/v0/device/battery/soc/set", data=json.dumps(data))
    if response.status_code != 200:
        print(f"** set_min() got response code {response.status_code}: {response.reason}")
        return None
    errno = response.json().get('errno')
    if errno != 0:
        if errno == 44096:
            print(f"** cannot update settings when schedule is active")
        else:
            print(f"** set_min(), {errno_message(response)}")
        return None
    return battery_settings

##################################################################################################
# get times and min soc settings and save in bat_settings
##################################################################################################

def get_settings():
    global battery_settings
    get_charge()
    get_min()
    return battery_settings

##################################################################################################
# get remote settings
##################################################################################################

def get_remote_settings(key='h115__17'):
    global token, device_id, debug_setting
    if get_device() is None:
        return None
    if debug_setting > 1:
        print(f"getting remote settings")
    if type(key) is list:
        values = {}
        for k in key:
            v = get_remote_settings(k)
            if v is not None:
                for x in v.keys():
                    values[x] = v[x]
        return values
    params = {'id': device_id, 'hasVersionHead': 1, 'key': key}
    response = signed_get(path="/c/v0/device/setting/get", params=params)
    if response.status_code != 200:
        print(f"** get_remote_settings() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_remote_settings(), no result data, {errno_message(response)}")
        return None
    values = result.get('values')
    if values is None:
        print(f"** get_remote_settings(), no values data")
        return None
    return values

def get_cell_temps():
    values = get_remote_settings('h115__17')
    cell_temps = []
    for k in sorted(values.keys()):
        t = c_float(values[k])
        if t > -50:
            cell_temps.append(t)
    return cell_temps

def get_cell_volts():
    values = get_remote_settings(['h115__14', 'h115__15', 'h115__16'])
    cell_volts = []
    for k in sorted(values.keys()):
        v = c_float(values[k])
        if v != 0:
            cell_volts.append(v)
    return cell_volts

##################################################################################################
# get work mode
##################################################################################################

work_mode = None

def get_work_mode():
    global token, device_id, work_mode, debug_setting
    if get_device() is None:
        return None
    if debug_setting > 1:
        print(f"getting work mode")
    params = {'id': device_id, 'hasVersionHead': 1, 'key': 'operation_mode__work_mode'}
    response = signed_get(path="/c/v0/device/setting/get", params=params)
    if response.status_code != 200:
        print(f"** get_work_mode() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_work_mode(), no result data, {errno_message(response)}")
        return None
    values = result.get('values')
    if values is None:
        print(f"** get_work_mode(), no work mode values data")
        return None
    work_mode = values.get('operation_mode__work_mode')
    if work_mode is None:
        print(f"** get_work_mode(), no work mode data")
        return None
    return work_mode

##################################################################################################
# set work mode
##################################################################################################

work_modes = ['SelfUse', 'Feedin', 'Backup', 'ForceCharge', 'ForceDischarge']
settable_modes = work_modes[:3]

def set_work_mode(mode, force = 0):
    global token, device_id, work_modes, work_mode, debug_setting
    if get_device() is None:
        return None
    if mode not in settable_modes:
        print(f"** work mode: must be one of {settable_modes}")
        return None
    if get_schedule().get('enable'):
        if force == 0:
            print(f"** set_work_mode(): cannot set work mode when a schedule is enabled")
            return None
        set_schedule(enable=0)
    if debug_setting > 1:
        print(f"set_work_mode(): {mode}")
        return None
    if debug_setting > 0:
        print(f"\nSetting work mode: {mode}")
    data = {'id': device_id, 'key': 'operation_mode__work_mode', 'values': {'operation_mode__work_mode': mode}, 'raw': ''}
    response = signed_post(path="/c/v0/device/setting/set", data=json.dumps(data))
    if response.status_code != 200:
        print(f"** set_work_mode() got response code {response.status_code}: {response.reason}")
        return None
    errno = response.json().get('errno')
    if errno != 0:
        if errno == 44096:
            print(f"** cannot update settings when schedule is active")
        else:
            print(f"** set_work_mode(), {errno_message(response)}")
        return None
    work_mode = mode
    return work_mode


##################################################################################################
# get flag
##################################################################################################

schedule = None

# get the current switch status
def get_flag():
    global device_sn, schedule, debug_setting
    if get_device() is None:
        return None
    if debug_setting > 1:
        print(f"getting flag")
    body = {'deviceSN': device_sn}
    response = signed_post(path="/op/v0/device/scheduler/get/flag", json=body)
    if response.status_code != 200:
        print(f"** get_flag() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_flag(), no result data, {errno_message(response)}")
        return None
    if schedule is None:
        schedule = {'enable': None, 'support': None, 'groups': None}
    schedule['enable'] = result.get('enable')
    schedule['support'] = result.get('support')
    return flag

##################################################################################################
# get schedule
##################################################################################################

# get the current schedule
def get_schedule():
    global device_sn, schedule, debug_setting
    if get_device() is None:
        return None
    if schedule is None or schedule.get('support') is None:
        get_flag()
        if schedule is None or schedule.get('support') is None or schedule['support'] == 0:
            print(f"** get_schedule() schedules are not available")
            return None
    if debug_setting > 1:
        print(f"getting schedule")
    body = {'deviceSN': device_sn}
    response = signed_post(path="/op/v0/device/scheduler/get", json=body)
    if response.status_code != 200:
        print(f"** get_schedule() got response code {response.status_code}: {response.reason}")
        return None
    result = response.json().get('result')
    if result is None:
        print(f"** get_schedule(), no result data, {errno_message(response)}")
        return None
    schedule['enable'] = result['enable']
    schedule['groups'] = result['groups']
    return schedule

##################################################################################################
# set schedule
##################################################################################################

# create time segment structure
def set_group(enable, start, end, mode, min_soc=10, fdsoc=10, fdpwr=0):
    if type(start) is str:
        start = time_hours(start)
    if type(end) is str:
        end = time_hours(end)
    if start is None or end is None:
        return None
    if mode not in work_modes:
        print(f"** mode must be one of {work_modes}")
        return None
    device_power = device.get('power')
    if device_power is None:
        device_power = 12000
    if fdpwr < 0 or fdpwr > 12000:
        print(f"** fdpwr must be between 0 and {device_power}")
        return None
    if fdsoc < 10 or fdsoc > 100:
        print(f"** fdsoc must between 10 and 100")
        return None
    start_hour, start_minute = split_hours(start)
    end_hour, end_minute = split_hours(end)
    group = {'enable': enable, 'startHour': start_hour, 'startMinute': start_minute, 'endHour': end_hour, 'endMinute': end_minute, 'workMode': mode, 'minSocOnGrid': min_soc, 'fdSoc': fdsoc, 'fdPwr': fdpwr}
    return group

# set a schedule from a period or list of time segment groups
def set_schedule(enable=1, groups=None):
    global token, device_sn, debug_setting, schedule
    if schedule is None:
        if get_schedule() is None:
            return None
    if debug_setting > 1:
        print(f"set_schedule(): enable = {enable}, groups = {groups}")
        return None
    if groups is not None:
        if type(groups) is not list:
            groups = [groups]
        body = {'deviceSN': deviceSN, 'groups': groups}
        if debug_setting > 0:
            print(f"\nSaving schedule")
        response = signed_post(path="/op/v0/device/scheduler/enable", json=body)
        if response.status_code != 200:
            print(f"** set_schedule() groups response code {response.status_code}: {response.reason}")
            return None
        errno = response.json().get('errno')
        if errno != 0:
            print(f"** set_schedule(), enable, {errno_message(response)}")
            return None
        schedule['groups'] = groups
    if debug_setting > 0:
        print(f"\nSetting flag to {enable}")
    body = {'deviceSN': deviceSN, 'enable': enable}
    response = signed_post(path="/op/v0/device/scheduler/set/flag", json=body)
    if response.status_code != 200:
        print(f"** set_schedule() flag response code {response.status_code}: {response.reason}")
        return None
    errno = response.json().get('errno')
    if errno != 0:
        print(f"** set_schedule(), flag, {errno_message(response)}")
        return None
    schedule['enable'] = enable
    return schedule


##################################################################################################
# get history data values
##################################################################################################
# returns a list of variables and their values / attributes
# time_span = 'hour', 'day', 'week'. For 'week', gets history of 7 days up to and including d
# d = day 'YYYY-MM-DD'. Can also include 'HH:MM' in 'hour' mode
# v = list of variables to get
# summary = 0: raw data, 1: add max, min, sum, 2: summarise and drop raw data, 3: calculate state
# save = "xxxxx": save the raw results to xxxxx_raw_<time_span>_<d>.json
# load = "<file>": load the raw results from <file>
# plot = 0: no plot, 1: plot variables separately, 2: combine variables 
##################################################################################################

# variables that cover inverter power data: generationPower must be first
power_vars = ['generationPower', 'feedinPower','loadsPower','gridConsumptionPower','batChargePower', 'batDischargePower', 'pvPower', 'meterPower2']
#  names after integration of power to energy. List must be in the same order as above. input_daily must be last
energy_vars = ['output_daily', 'feedin_daily', 'load_daily', 'grid_daily', 'bat_charge_daily', 'bat_discharge_daily', 'pv_energy_daily', 'ct2_daily', 'input_daily']

def get_history(time_span='hour', d=None, v=None, summary=1, save=None, load=None, plot=0):
    global token, device_sn, debug_setting, raw_vars, off_peak1, off_peak2, peak, flip_ct2, tariff, max_power_kw
    if get_device() is None:
        return None
    time_span = time_span.lower()
    if d is None:
        d = datetime.strftime(datetime.now() - timedelta(minutes=5), "%Y-%m-%d %H:%M:%S" if time_span == 'hour' else "%Y-%m-%d")
    if time_span == 'week' or type(d) is list:
        days = d if type(d) is list else date_list(e=d, span='week',today=True)
        result_list = []
        for day in days:
            result = get_history('day', d=day, v=v, summary=summary, save=save, plot=0)
            if result is None:
                return None
            result_list += result
        if plot > 0:
            plot_raw(result_list, plot, station)
        return result_list
    if v is None:
        if raw_vars is None:
            raw_vars = get_vars()
        v = [x['variable'] for x in raw_vars]
    elif type(v) is not list:
        v = [v]
    for var in v:
        if var not in [x['variable'] for x in raw_vars]:
            print(f"** get_history(): invalid variable '{var}'")
            print(f"{[x['variable'] for x in raw_vars]}")
            return None
    if debug_setting > 1:
        print(f"getting history data")
    if load is None:
        (t_begin, t_end) = query_time(d, time_span)
        body = {'sn': device_sn, 'variables': v, 'begin': t_begin, 'end': t_end}
        response = signed_post(path="/op/v0/device/history/query", json=body)
        if response.status_code != 200:
            print(f"** get_history() got response code {response.status_code}: {response.reason}")
            return None
        result = response.json().get('result')
        errno = response.json().get('errno')
        if errno > 0 or result is None or len(result) == 0:
            print(f"** get_history(), no data, {errno_message(response)}")
            return None
        result = result[0].get('datas')
    else:
        file = open(load)
        result = json.load(file)
        file.close()
    if save is not None:
        file_name = save + "_history_" + time_span + "_" + d[0:10].replace('-','') + ".txt"
        file = open(file_name, 'w')
        json.dump(result, file, indent=4, ensure_ascii= False)
        file.close()
    for var in result:
        var['date'] = d[0:10]
        if var.get('unit') is None:
            var['unit'] = '-'
    if summary <= 0 or time_span == 'hour':
        if summary == -1:     # return last value only for each variable
            for v in result:
                v['time'] = v['data'][-1]['time'][11:16]
                v['value'] = v['data'][-1]['value']
                del v['data']
#                del v['date']
        elif plot > 0:
            plot_raw(result, plot)
        return result
    # integrate kW to kWh based on 5 minute samples
    if debug_setting > 1:
        print(f"calculating summary data")
    # copy generationPower to produce inputPower data
    input_name = None
    if 'generationPower' in v:
        input_name = energy_vars[-1]
        input_result = deepcopy(result[v.index('generationPower')])
        input_result['name'] = input_name
        for y in input_result['data']:
            y['value'] = -y['value'] if y['value'] < 0.0 else 0.0
        result.append(input_result)
    for var in result:
        energy = var['unit'] == 'kW' if var.get('unit') is not None else False
        hour = 0
        if energy:
            kwh = 0.0       # kwh total
            kwh_off = 0.0   # kwh during off peak time (02:00-05:00)
            kwh_peak = 0.0  # kwh during peak time (16:00-19:00)
            kwh_neg = 0.0
            if len(var['data']) > 1:
                sample_time = round(60 * (time_hours(var['data'][1]['time'][11:19]) - time_hours(var['data'][0]['time'][11:19])), 2)
            else:
                sample_time = 5.0
            if debug_setting > 1:
                print(f"sample_time = {sample_time} minutes")
        sum = 0.0
        count = 0
        max = None
        max_time = None
        min = None
        min_time = None
        if summary == 3 and energy:
            var['state'] = [{}]
        for y in var['data']:
            h = time_hours(y['time'][11:19]) # time
            value = y['value']
            sum += value
            count += 1
            max = value if max is None or value > max else max
            min = value if min is None or value < min else min
            if energy:
                e = value * sample_time / 60      # convert kW samples to kWh energy
                if e > 0.0:
                    kwh += e
                    if tariff is not None:
                        if hour_in (h, tariff['off_peak1']):
                            kwh_off += e
                        elif hour_in(h, tariff['off_peak2']):
                            kwh_off += e
                        elif hour_in(h, tariff['peak']):
                            kwh_peak += e
                        elif hour_in(h, tariff['peak2']):
                            kwh_peak += e
                else:
                    kwh_neg -= e
                if summary == 3:
                    if int(h) > hour:    # new hour
                        var['state'].append({})
                        hour += 1
                    var['state'][hour]['time'] = y['time'][11:16]
                    var['state'][hour]['state'] = kwh
                var['kwh'] = kwh
                var['kwh_off'] = kwh_off
                var['kwh_peak'] = kwh_peak
                var['kwh_neg'] = kwh_neg
        var['count'] = count
        var['average'] = sum / count if count > 0 else None
        var['max'] = max if max is not None else None
        var['max_time'] = var['data'][[y['value'] for y in var['data']].index(max)]['time'][11:16] if max is not None else None
        var['min'] = min if min is not None else None
        var['min_time'] = var['data'][[y['value'] for y in var['data']].index(min)]['time'][11:16] if min is not None else None
        if summary >= 2:
            if energy and var['variable'] in power_vars and (input_name is None or var['name'] != input_name):
                var['name'] = energy_vars[power_vars.index(var['variable'])]
            if energy:
                var['unit'] = 'kWh'
            del var['data']
    if plot > 0 and summary < 2:
        plot_history(result, plot)
    return result

# plot raw results data
def plot_history(result, plot=1):
    global site, device_sn, legend_location
    if result is None:
        return
    # work out what we have
    units = []
    vars = []
    dates = []
    for v in result:
        if v.get('data') is not None:
            if v['unit'] not in units:
                units.append(v['unit'])
            if v['variable'] not in vars:
                vars.append(v['variable'])
            if v['date'] not in dates:
                dates.append(v['date'])
    dates = sorted(dates)
    if len(vars) == 0 or len(dates) == 0:
        return
    # plot variables by date with the same units on the same charts
    for unit in units:
        lines = 0
        for d in dates:
            # get time labels for X axix
            if lines == 0:
                labels = []
                for v in [v for v in result if v['unit'] == unit and v['date'] == d]:
                    h = 0
                    i = 0
                    while h < 24:
                        h = int(v['data'][i]['time'][11:13]) if i < len(v['data']) else h + 1
                        labels.append(f"{h:02d}:00")
                        i += 12
                    break
                plt.figure(figsize=(figure_width, figure_width/3))
                plt.xticks(ticks=range(0, len(labels)), labels=labels, rotation=90, fontsize=8)
                plt.xlim(-1, len(labels))
            for v in [v for v in result if v['unit'] == unit and v['date'] == d]:
                n = len(v['data'])
                x =[]
                h = 0
                old_minute = 0
                for i in range(0,n):
                    new_minute = int(v['data'][i]['time'][14:16])
                    h += 1 if new_minute < old_minute else 0
                    x.append(h + new_minute / 60)
                    old_minute = new_minute
                y = [v['data'][i]['value'] for i in range(0, n)]
                name = v['name']
                label = f"{name} / {d}" if plot == 2 and len(dates) > 1 else name
                plt.plot(x, y ,label=label)
                lines += 1
            if lines >= 1 and (plot == 1 or d == dates[-1]) :
                if lines > 1:
                    plt.legend(fontsize=6, loc=legend_location)
                title = ""
                if plot == 1 or len(dates) == 1 or lines == 1:
                    title = f"{d} / "
                if len(vars) == 1 or lines == 1:
                    title = f"{name} / {title}"
                title = f"{title}{unit} / {device_sn}"
                plt.title(title, fontsize=12)
                plt.grid()
                plt.show()
                lines = 0
    return

##################################################################################################
# get production report in kWh
##################################################################################################
# dimension = 'day', 'week', 'month', 'year'
# d = day 'YYYY-MM-DD'
# v = list of report variables to get
# summary = 0, 1, 2: do a quick total energy report for a day
# save = "xxxxx": save the report results to xxxxx_raw_<time_span>_<d>.json
# load = "<file>": load the report results from <file>
# plot = 0: no plot, 1 = plot variables separately, 2 = combine variables
##################################################################################################

report_vars = ['generation', 'feedin', 'loads', 'gridConsumption', 'chargeEnergyToTal', 'dischargeEnergyToTal']
report_names = ['Generation', 'Grid Export', 'Consumption', 'Grid Import', 'Battery Charge', 'Battery Discharge']

# fix power values after fox corrupts high word of 32-bit energy total
fix_values = 1
fix_value_threshold = 200000000.0
fix_value_mask = 0x0000FFFF

def get_report(dimension='day', d=None, v=None, summary=1, save=None, load=None, plot=0):
    global token, device_sn, var_list, debug_setting, report_vars
    # process list of days
    if d is not None and type(d) is list:
        result_list = []
        for day in d:
            result = get_report(dimension, d=day, v=v, summary=summary, save=save, load=load, plot=0)
            if result is None:
                return None
            result_list += result
        if plot > 0:
            plot_report(result_list, plot, station)
        return result_list
    # validate parameters
    dimension = dimension.lower()
    summary = 1 if summary == True else 0 if summary == False else summary
    if summary == 2 and dimension != 'day':
        summary = 1
    if summary == 0 and dimension == 'week':
        dimension = 'day'
    if d is None:
        d = datetime.strftime(datetime.now(), "%Y-%m-%d")
    if v is None:
        v = report_vars
    elif type(v) is not list:
        v = [v]
    for var in v:
        if var not in report_vars:
            print(f"** get_report(): invalid variable '{var}'")
            print(f"{report_vars}")
            return None
    if debug_setting > 1:
        print(f"getting report data")
    current_date = query_date(None)
    main_date = query_date(d)
    side_result = None
    if dimension in ('day', 'week') and summary > 0:
        # side report needed
        side_date = query_date(d, -7) if dimension == 'week' else main_date
        if dimension == 'day' or main_date['month'] != side_date['month']:
            body = {'sn': device_sn, 'dimension': 'month', 'variables': v, 'year': side_date['year'], 'month': side_date['month'], 'day': side_date['day']}
            response = signed_post(path="/op/v0/device/report/query", json=body)
            if response.status_code != 200:
                print(f"** get_report() side report got response code {response.status_code}: {response.reason}")
                return None
            side_result = response.json().get('result')
            errno = response.json().get('errno')
            if errno > 0 or side_result is None or len(side_result) == 0:
                print(f"** get_report(), no report data available, {errno_message(response)}")
                return None
            if fix_values == 1:
                for var in side_result:
                    for data in var['values']:
                        if data['value'] > fix_value_threshold:
                            data['value'] = (int(data['value'] * 10) & fix_value_mask) / 10
    if summary < 2:
        body = {'sn': device_sn, 'dimension': dimension.replace('week', 'month'), 'variables': v, 'year': main_date['year'], 'month': main_date['month'], 'day': main_date['day']}
        response = signed_post(path="/op/v0/device/report/query", json=body)
        if response.status_code != 200:
            print(f"** get_report() main report got response code {response.status_code}: {response.reason}")
            return None
        result = response.json().get('result')
        errno = response.json().get('errno')
        if errno > 0 or result is None or len(result) == 0:
            print(f"** get_report(), no report data available, {errno_message(response)}")
            return None
        # correct errors in report values:
        if fix_values == 1:
            for var in result:
                for data in var['values']:
                    if data['value'] > fix_value_threshold:
                        data['value'] = (int(data['value'] * 10) & fix_value_mask) / 10
        # prune results back to only valid, complete data for day, week, month or year
        if dimension == 'day' and main_date['year'] == current_date['year'] and main_date['month'] == current_date['month'] and main_date['day'] == current_date['day']:
            for var in result:
                # prune current day to hours that are valid
                var['values'] = var['values'][:int(current_date['hour'])]
        if dimension == 'week':
            for i, var in enumerate(result):
                # prune results to days required
                var['values'] = var['values'][:int(main_date['day'])]
                if side_result is not None:
                    # prepend side results (previous month) if required
                    var['values'] = side_result[i]['values'][int(side_date['day']):] + var['data']
                # prune to week required
                var['data'] = var['data'][-7:]
        elif dimension == 'month' and main_date['year'] == current_date['year'] and main_date['month'] == current_date['month']:
            for var in result:
                # prune current month to days that are valid
                var['values'] = var['values'][:int(current_date['day'])]
        elif dimension == 'year' and main_date['year'] == current_date['year']:
            for var in result:
                # prune current year to months that are valid
                var['values'] = var['values'][:int(current_date['month'])]
    else:
        # fake result for summary only report
        result = []
        for x in v:
            result.append({'variable': x, 'data': [], 'date': d})
    if load is not None:
        file = open(load)
        result = json.load(file)
        file.close()
    elif save is not None:
        file_name = save + "_report_" + dimension + "_" + d.replace('-','') + ".txt"
        file = open(file_name, 'w')
        json.dump(result, file, indent=4, ensure_ascii= False)
        file.close()
    if summary == 0:
        return result
    # calculate and add summary data
    for i, var in enumerate(result):
        count = 0
        sum = 0.0
        max = None
        min = None
        for y in var['values']:
            value = y['value']
            count += 1
            sum += value
            max = value if max is None or value > max else max
            min = value if min is None or value < min else min
        # correct day total from side report
        var['total'] = sum if dimension != 'day' else side_result[i]['data'][int(main_date['day'])-1]['value']
        var['name'] = report_names[report_vars.index(var['variable'])]
        var['type'] = dimension
        if summary < 2:
            var['sum'] = sum
            var['average'] = var['total'] / count if count > 0 else None
            var['date'] = d
            var['count'] = count
            var['max'] = max if max is not None else None
            var['max_index'] = [y['value'] for y in var['data']].index(max) if max is not None else None
            var['min'] = min if min is not None else None
            var['min_index'] = [y['value'] for y in var['data']].index(min) if min is not None else None
    if plot > 0 and summary < 2:
        plot_report(result, plot, station)
    return result

# plot get_report result
def plot_report(result, plot=1, station=0):
    global site, device_sn
    if result is None:
        return
    # work out what we have
    vars = []
    types = []
    index = []
    dates = []
    for v in result:
        if v.get('values') is not None:
            if v['variable'] not in vars:
                vars.append(v['variable'])
            if v['type'] not in types:
                types.append(v['type'])
            if v['date'] not in dates:
                dates.append(v['date'])
            for i in [x['index'] for x in v['data']]:
                if i not in index:
                    index.append(i)
#    print(f"vars = {vars}, dates = {dates}, types = {types}, index = {index}")
    if len(vars) == 0:
        return
    # plot variables by date with the same units on the same charts
    lines = 0
    width = 0.8 / (len(dates) if plot == 1 else len(vars) if len(dates) == 1 else len(dates))
    align = 0.0
    for var in vars:
        if lines == 0:
            plt.figure(figsize=(figure_width, figure_width/3))
            if types[0] == 'day':
                plt.xticks(ticks=index, labels=[hours_time(h) for h in range(0,len(index))], rotation=90, fontsize=8)
            if types[0] == 'week':
                plt.xticks(ticks=range(1,8), labels=date_list(span='week', e=dates[0], today=2), rotation=45, fontsize=8, ha='right', rotation_mode='anchor')
            elif types[0] == 'month':
                plt.xticks(ticks=index, labels=date_list(s=dates[0][:-2]+'01', limit=len(index), today=2), rotation=45, fontsize=8, ha='right', rotation_mode='anchor')
            elif types[0] == 'year':
                plt.xticks(ticks=index, labels=[m[:3] for m in month_names[:len(index)]], rotation=45, fontsize=10, ha='right', rotation_mode='anchor')
        for v in [v for v in result if v['variable'] == var]:
            name = v['name']
            d = v['date']
            n = len(v['values'])
            x = [i + align  for i in range(1, n+1)]
            y = [v['values'][i]['value'] for i in range(0, n)]
            label = f"{d}" if len(dates) > 1 else f"{name}"
            plt.bar(x, y ,label=label, width=width)
            align += width
            lines += 1
        if lines >= 1 and (plot == 1 or len(dates) > 1 or var == vars[-1]):
            if lines > 1:
                plt.legend(fontsize=6, loc=legend_location)
            title = ""
            if types[0] == 'day' and (lines == 1 or len(dates) == 1):
                title = f"{d} / "
            elif types[0] == 'week':
                title = f"Week to {d} / "
            elif types[0] == 'month':
                title = f"Month of {month_names[int(d[5:7])-1]} {d[:4]} / "
            elif types[0] == 'year':
                title = f"Year {d[:4]} / "
            if len(vars) == 1 or plot == 1 or len(dates) > 1:
                title = f"{name} / {title}kWh / "
            else:
                title = f"{title} kWh / "
            title = f"{title}{site['name'] if station == 1 else device_sn}"
            plt.title(title, fontsize=12)
            plt.grid()
            plt.show()
            lines = 0
            align = -0.4
    return


##################################################################################################
##################################################################################################
# Operations section
##################################################################################################
##################################################################################################

##################################################################################################
# Time and charge period functions
##################################################################################################
# times are held either as text HH:MM or HH:MM:SS or as decimal hours e.g. 01.:30 = 1.5
# decimal hours allows maths operations to be performed simply

# time shift from UTC (before any DST adjustment)
time_shift = 0

# roll over decimal times after maths and round to 1 minute
def round_time(h):
    if h is None:
        return None
    while h < 0:
        h += 24
    while h >= 24:
        h -= 24
    return int(h) + int(60 * (h - int(h)) + 0.5) / 60

# split decimal hours into hours and minutes
def split_hours(h):
    if h is None:
        return (None, None)
    hours = int(h % 24)
    minutes = int (h % 1 * 60 + 0.5)
    return (hours, minutes)

# convert time string HH:MM:SS to decimal hours
def time_hours(t, d = None):
    if t is None:
        t = d
    if type(t) is float:
        return t
    elif type(t) is int:
        return float(t)
    elif type(t) is str and t.replace(':', '').isnumeric() and t.count(':') <= 2:
        t += ':00' if t.count(':') == 1 else ''
        return sum(float(t) / x for x, t in zip([1, 60, 3600], t.split(":")))
    print(f"** invalid time string {t}")
    return None

# convert decimal hours to time string HH:MM:SS
def hours_time(h, ss = False, day = False, mm = True):
    if h is None:
        return "None"
    n = 8 if ss else 5 if mm else 2
    d = 0
    while h < 0:
        h += 24
        d -= 1
    while h >= 24:
        h -= 24
        d += 1
    suffix = ""
    if day:
        suffix = f"/{d:0}"
    return f"{int(h):02}:{int(h * 60 % 60):02}:{int(h * 3600 % 60):02}"[:n] + suffix

# True if a decimal hour is within a time period
def hour_in(h, period):
    if period is None:
        return False
    s = period['start']
    e = period['end']
    if s is None or e is None or s == e:
        return False
    while h < 0:
        h += 24
    while h >= 24:
        h -= 24
    if s > e:
        # e.g. 16:00 - 07:00
        return h >= s or h < e
    else:
        # e.g. 02:00 - 05:00
        return h >= s and h < e

# Return the hours in a time period with optional value check
def period_hours(period, check = None, value = 1):
    if period is None:
        return 0
    if check is not None and period[check] != value:
        return 0
    return round_time(period['end'] - period['start'])

def format_period(period):
    return f"{hours_time(period['start'])} - {hours_time(period['end'])}"

#work out if a date falls in BST or GMT. Returns 1 for BST, 0 for GMT
def british_summer_time(d=None):
    if type(d) is list:
        l = []
        for x in d:
            l.append(british_summer_time(x))
        return l
    elif type(d) is str:
        dat = datetime.strptime(d[:10], '%Y-%m-%d')
        hour = int(d[11:13]) if len(d) >= 16 else 12
    else:
        now = datetime.now(tz=timezone.utc)
        dat =  d.date() if d is not None else now.date()
        hour = d.hour if d is not None else now.hour 
    start_date = dat.replace(month=3, day=31)
    days = (start_date.weekday() + 1) % 7
    start_date = start_date - timedelta(days=days)
    end_date = dat.replace(month=10, day=31)
    days = (end_date.weekday() + 1) % 7
    end_date = end_date - timedelta(days=days)
    if dat == start_date and hour < 1:
        return 0
    elif dat == end_date and hour < 1:
        return 1
    elif dat >= start_date and dat < end_date:
        return 1
    return 0

# hook for alternative daylight saving methods
daylight_saving = british_summer_time

# helper function to return change in daylight saving between 2 datetimes
def daylight_changes(a,b):
    return daylight_saving(a) - daylight_saving(b)


##################################################################################################
# TARIFFS - charge periods and time of user (TOU)
# time values are decimal hours
##################################################################################################

# time periods for Octopus Flux
octopus_flux = {
    'name': 'Octopus Flux',
    'off_peak1': {'start': 2.0, 'end': 5.0, 'force': 1},        # off-peak period 1 / am charging period
    'off_peak2': {'start': 0.0, 'end': 0.0, 'force': 0},        # off-peak period 2 / pm charging period
    'peak': {'start': 16.0, 'end': 19.0 },                      # peak period 1
    'peak2': {'start': 0.0, 'end': 0.0 },                       # peak period 2
    'default_mode': 'SelfUse',                                  # default work mode
    'Feedin': {'start': 16.0, 'end': 7.0, 'min_soc': 75},       # when feedin work mode is set
    'forecast_times': [21, 22, 23]                              # hours in a day to get a forecast
    }

# time periods for Intelligent Octopus
intelligent_octopus = {
    'name': 'Intelligent Octopus',
    'off_peak1': {'start': 23.5, 'end': 5.5, 'force': 1},
    'off_peak2': {'start': 0.0, 'end': 0.0, 'force': 0},
    'peak': {'start': 0.0, 'end': 0.0 },
    'peak2': {'start': 0.0, 'end': 0.0 },
    'forecast_times': [22, 23]
    }

# time periods for Octopus Cosy
octopus_cosy = {
    'name': 'Octopus Cosy',
    'off_peak1': {'start': 4.0, 'end': 7.0, 'force': 1},
    'off_peak2': {'start': 13.0, 'end': 16.0, 'force': 0},
    'peak': {'start': 16.0, 'end': 19.0 },
    'peak2': {'start': 0.0, 'end': 0.0 },
    'forecast_times': [2, 3, 12]
    }

# time periods for Octopus Go
octopus_go = {
    'name': 'Octopus Go',
    'off_peak1': {'start': 0.5, 'end': 4.5, 'force': 1},
    'off_peak2': {'start': 0.0, 'end': 0.0, 'force': 0},
    'peak': {'start': 0.0, 'end': 0.0 },
    'peak2': {'start': 0.0, 'end': 0.0 },
    'forecast_times': [22, 23]
    }

# time periods for Agile Octopus
agile_octopus = {
    'name': 'Agile Octopus',
    'off_peak1': {'start': 2.5, 'end': 5.0, 'force': 1},
    'off_peak2': {'start': 0.0, 'end': 0.0, 'force': 0},
    'peak': {'start': 16.0, 'end': 19.0 },
    'peak2': {'start': 0.0, 'end': 0.0 },
    'forecast_times': [22, 23]
    }

# time periods for British Gas Electric Driver
bg_driver = {
    'name': 'British Gas Electric Driver',
    'off_peak1': {'start': 0.0, 'end': 5.0, 'force': 1},
    'off_peak2': {'start': 0.0, 'end': 0.0, 'force': 0},
    'peak': {'start': 0.0, 'end': 0.0 },
    'peak2': {'start': 0.0, 'end': 0.0 },
    'forecast_times': [22, 23]
    }

# custom time periods / template
custom_periods = {'name': 'Custom',
    'off_peak1': {'start': 2.0, 'end': 5.0, 'force': 1},
    'off_peak2': {'start': 15.0, 'end': 16.0, 'force': 0},
    'peak': {'start': 16.0, 'end': 19.0 },
    'peak2': {'start': 0.0, 'end': 0.0 },
    'forecast_times': [22, 23]
    }

tariff_list = [octopus_flux, intelligent_octopus, octopus_cosy, octopus_go, agile_octopus, bg_driver, custom_periods]

##################################################################################################
# Octopus Energy Agile Price
##################################################################################################

# base settings
octopus_api_url = "https://api.octopus.energy/v1/products/%PRODUCT%/electricity-tariffs/E-1R-%PRODUCT%-%REGION%/standard-unit-rates/"
regions = {'A':'Eastern England', 'B':'East Midlands', 'C':'London', 'D':'Merseyside and Northern Wales', 'E':'West Midlands', 'F':'North Eastern England', 'G':'North Western England', 'H':'Southern England',
    'J':'South Eastern England', 'K':'Southern Wales', 'L':'South Western England', 'M':'Yorkshire', 'N':'Southern Scotland', 'P':'Northern Scotland'}


# preset weightings for average pricing over charging duration:
front_loaded = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]           # 3 hour average, front loaded
first_hour =   [1.0, 1.0]                               # lowest average price for first hour

tariff_config = {
    'product': "AGILE-FLEX-22-11-25",     # product code to use for Octopus API
    'region': "H",                        # region code to use for Octopus API
    'duration': 3,                        # decimal hours for charge period
    'start_at': 23,                       # earliest start time for charge period
    'end_by': 8,                          # latest end time for charge period
    'update_time': 16.5,                  # time in hours when tomrow's data can be fetched
    'weighting': None                     # weights for weighted average
}

# get prices and work out lowest weighted average price time period
def get_agile_period(d=None):
    global debug_setting, octopus_api_url, time_shift
    # get time, dates and duration
    duration = tariff_config['duration']
    duration = 3 if duration is None else 6 if duration > 6 else 1 if duration < 1 else duration
    # round up to 30 minutes so charge periods covers complete end pricing period
    duration = round(duration * 2 + 0.49, 0) / 2
    # number of 30 minute pricing periods
    span = int(duration * 2)
    # work out dates for price forecast
    if d is not None and len(d) < 11:
        d += " 18:00"
    # get dates and times
    system_time = (datetime.now(tz=timezone.utc) + timedelta(hours=time_shift)) if d is None else datetime.strptime(d, '%Y-%m-%d %H:%M')
    time_offset = daylight_saving(system_time) if daylight_saving is not None else 0
    # adjust system to get local time now
    now = system_time + timedelta(hours=time_offset)
    hour_now = now.hour + now.minute / 60
    update_time = tariff_config['update_time']
    update_time = time_hours(update_time) if type(update_time) is str else 17 if update_time is None else update_time
    today = datetime.strftime(now + timedelta(days=0 if hour_now >= update_time else -1), '%Y-%m-%d')
    tomorrow = datetime.strftime(now + timedelta(days=1 if hour_now >= update_time else 0), '%Y-%m-%d')
    if debug_setting > 0:
        print(f"  datetime = {today} {hours_time(hour_now)}")
    # get product and region
    product = tariff_config['product'].upper()
    region = tariff_config['region'].upper()
    if region not in regions:
        print(f"** region {region} not recognised, valid regions are {regions}")
        return None
    # get prices from 11pm today to 11pm tomorrow
    print(f"\nProduct: {product}")
    print(f"Region:  {regions[region]}")
    zulu_hour = "T" + hours_time(23 - time_offset - time_shift, ss=True) + "Z"
    url = octopus_api_url.replace("%PRODUCT%", product).replace("%REGION%", region)
    period_from = today + zulu_hour
    period_to = tomorrow + zulu_hour
    params = {'period_from': period_from, 'period_to': period_to }
    if debug_setting > 1:
        print(f"time_offset = {time_offset}, time_shift = {time_shift}")
        print(f"period_from = {period_from}, period_to = {period_to}")
    response = requests.get(url, params=params)
    if response.status_code != 200:
        print(f"** get_agile_period() response code from Octopus API {response.status_code}: {response.reason}")
        return None
    # results are in reverse chronological order...
    results = response.json().get('results')[::-1]
    # extract times and prices
    times = []          # ordered list of times
    prices = []         # ordered list of prices inc VAT
    for r in results:
        time_offset = daylight_saving(r['valid_from'][:16]) if daylight_saving is not None else 0
        times.append(hours_time(time_hours(r['valid_from'][11:16]) + time_offset + time_shift))
        prices.append(r['value_inc_vat'])
    # work out start and end times for charging
    start_at = tariff_config['start_at']
    start_at = time_hours(start_at) if type(start_at) is str else 23.0 if start_at is None else start_at
    end_by = tariff_config['end_by']
    end_by = time_hours(end_by) if type(end_by) is str else 8.0 if end_by is None else end_by
    start_i = int(round_time(start_at - 23) * 2)
    end_i = int(round_time(end_by - 23 - duration) * 2)
    end_i = start_i if end_i < start_i else end_i
    if debug_setting > 1:
        print(f"start_at = {start_at}, end_by = {end_by}, start_i = {start_i}, end_i = {end_i}")
    if len(results) < (end_i + span):
        print(f"** get_agile_period(): prices not available for {tomorrow}")
        return None
    # work out weighted average for each period and track lowest price
    period = {}
    min_i = None
    min_v = None
    weighting = tariff_config['weighting']
    weights = [1.0] * span if weighting is None else (weighting + [0.0] * span)[:span]
    for i in range(start_i, end_i + 1):
        start = times[i]
        p_span = prices[i: i + span]
        wavg = round(sum(p * w for p,w in zip(p_span, weights)) / sum(weights), 2)
        if debug_setting > 1:
            print(f"  {start}: {wavg:.2f}")
        if min_v is None or wavg < min_v:
            min_v = wavg
            min_i = i
    # save results
    start = times[min_i]
    end = times[min_i + span]
    price = min_v
    period['date'] = tomorrow
    period['times'] = times
    period['prices'] = prices
    period['span'] = span
    period['start'] = start
    period['end'] = end
    period['price'] = price
    return period


# set tariff and charge time period based on pricing for Agile Octopus
def set_agile_period(d=None):
    global debug_setting, agile_octopus
    period = get_agile_period(d=d)
    if period is None:
        return None
    tomorrow = period['date']
    s = f"\nPrices for {tomorrow} AM charging period (p/kWh inc VAT):\n" + " " * 4 * 15
    for i in range(0, len(period['times'])):
        s += "\n" if i % 6 == 2 else ""
        s += f"  {period['times'][i]} = {period['prices'][i]:5.2f}"
    print(s)
    weighting = tariff_config['weighting']
    if weighting is not None:
        print(f"\nWeighting: {weighting}")
    start = period['start']
    end = period['end']
    price = period['price']
    duration = tariff_config['duration']
    print(f"\nBest {duration} hour AM charging period:")
    print(f"  Time:  {start} to {end}")
    print(f"  Price: {price:.2f} p/kWh inc VAT")
    agile_octopus['off_peak1']['start'] = time_hours(start)
    agile_octopus['off_peak1']['end'] = time_hours(end)
    print(f"\nAM charging period set for {agile_octopus['name']}")
    return period

tariff = octopus_flux

# set tariff and charge time period based on pricing for Agile Octopus
def set_tariff(find, update=1, **settings):
    global debug_setting, agile_octopus, tariff, tariff_list
    print(f"\n---------------- set_tariff -----------------")
    # validate parameters
    args = locals()
    s = ""
    for k in [k for k in args.keys() if args[k] is not None and k != 'settings']:
        s += f"\n  {k} = {args[k]}"
    # store settings:
    for key, value in settings.items():
        if key not in tariff_config:
            print(f"** unknown configuration parameter: {key}")
        else:
            tariff_config[key] = value
            s += f"\n  {key} = {value}"
    if len(s) > 0:
        print(f"Parameters: {s}")
    found = []
    if find in tariff_list:
        found = [find]
    elif type(find) is str:
        for dict in tariff_list:
            if find.lower() in dict['name'].lower():
                found.append(dict)
    if len(found) != 1:
        print(f"** set_tariff(): {find} must identify one of the available tariffs:")
        for x in tariff_list:
            print(f"  {x['name']}")
        return None
    use = found[0]
    if use == agile_octopus:
        period = set_agile_period()
        if period is None:
            return None
    if update == 1:
        tariff = use
        print(f"\nTariff set to {tariff['name']}")
    elif debug_setting > 0:
        print(f"\nNo changes made to current tariff")
    return None


##################################################################################################
# CHARGE_NEEDED - calculate charge from current battery charge, forecast yield and expected load
##################################################################################################

# how consumption varies by month across a year. 12 values.
# month                J   F   M   A   M   J   J   A   S   O   N   D
high_seasonality =   [13, 12, 11, 10,  9,  8,  9,  9, 10, 11, 12, 13]
medium_seasonality = [11, 11, 10, 10,  9,  9,  9,  9, 10, 10, 11, 12]
no_seasonality =     [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
seasonality_list = [high_seasonality, medium_seasonality, no_seasonality]
seasonality = medium_seasonality

# how consumption varies by hour across a day. 24 values.
# from hour       00  01  02  03  04  05  06  07  08  09  10  11  12  13  14  15  16  17  18  19  20  21  22  23
high_profile =   [20, 20, 20, 20, 20, 20, 40, 50, 70, 70, 70, 50, 50, 50, 50, 70, 99, 99, 99, 70, 40, 35, 30, 30]
medium_profile = [28, 28, 28, 28, 28, 28, 36, 49, 65, 70, 65, 49, 44, 44, 49, 63, 92, 99, 92, 63, 47, 39, 33, 31]
no_profile =     [50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50]
daily_consumption = medium_profile

# how generation varies by hour across a day and by season. 24 values
winter_sun =     [ 0,  0,  0,  0,  0,  0,  0,  0,  5, 20, 55, 85, 99, 85, 55, 20,  5,  0,  0,  0,  0,  0,  0,  0]
spring_sun =     [ 0,  0,  0,  0,  0,  0,  0,  5, 19, 40, 70, 90, 99, 90, 70, 40, 19,  5,  0,  0,  0,  0,  0,  0]
summer_sun =     [ 0,  0,  0,  0,  0,  0,  5, 15, 30, 50, 80, 95, 99, 95, 80, 50, 30, 15,  5,  0,  0,  0,  0,  0]
autumn_sun =     [ 0,  0,  0,  0,  0,  0,  0,  5, 19, 40, 70, 90, 99, 90, 70, 40, 19,  5,  0,  0,  0,  0,  0,  0]

# map month via quarters to seasons
seasonal_sun =   [
    {'name': 'Winter', 'sun': winter_sun},
    {'name': 'Spring', 'sun': spring_sun},
    {'name': 'Summer', 'sun': summer_sun},
    {'name': 'Autumn', 'sun': autumn_sun},
]
# --------- helper functions for charge_needed() ---------

# rotate 24 hour list so it aligns with hour_now and cover run_time:
def timed_list(data, hour_now, run_time=None):
    h = int(hour_now)
    data1 = data[h:] + data[:h]
    if run_time is not None:
        data1 = (data1 + data1)[:run_time]
    return data1

# take a report and return (average value and 24 hour profile)
def report_value_profile(result):
    if type(result) is not list or result[0]['type'] != 'day':
        return (None, None)
    data = []
    for h in range(0,24):
        data.append((0.0, 0)) # value sum, count of values
    totals = 0
    n = 0
    for day in result:
        hours = 0
        # sum and count available values by hour
        for i in range(0, len(day['data'])):
            data[i] = (data[i][0] + day['data'][i]['value'], data[i][1]+1)
            hours += 1
        totals += day['total'] * (24 / hours if hours >= 1 else 1)
        n += 1
    daily_average = totals / n if n !=0 else None
    # average for each hour
    by_hour = []
    for h in data:
        by_hour.append(h[0] / h[1] if h[1] != 0 else 0.0)   # sum / count
    if daily_average is None or daily_average == 0.0:
        return (None, None)
    # rescale to match daily_average
    current_total = sum(by_hour)
    return (daily_average, [h * daily_average / current_total for h in by_hour])

# take forecast and return (value and timed profile)
def forecast_value_timed(forecast, today, tomorrow, hour_now, run_time, time_offset=0):
    value = forecast.daily[tomorrow]['kwh']
    profile = []
    for h in range(0, 24):
        profile.append(c_float(forecast.daily[tomorrow]['hourly'].get(int(round_time(h - time_offset)))))
    timed = []
    for h in range(int(hour_now), 24):
        timed.append(c_float(forecast.daily[today]['hourly'].get(int(round_time(h - time_offset)))))
    return (value, (timed + profile + profile)[:run_time])

# Battery open circuit voltage (OCV) from 0% to 100% SoC
#                 0%     10%    20%    30%    40%    50%    60%    70%    80%    90%   100%
lifepo4_curve = [51.31, 51.84, 52.41, 52.45, 52.50, 52.64, 52.97, 53.10, 53.16, 53.63, 55.00]

# charge_needed settings
charge_config = {
    'contingency': 20,                # % of consumption to allow as contingency
    'capacity': None,                 # Battery capacity (over-ride)
    'min_soc': None,                  # Minimum Soc (over-ride)
    'charge_current': None,           # max battery charge current setting in A
    'discharge_current': None,        # max battery discharge current setting in A
    'export_limit': None,             # maximum export power
    'discharge_loss': 0.97,           # loss converting battery discharge power to grid power
    'pv_loss': 0.95,                  # loss converting PV power to battery charge power
    'grid_loss': 0.95,                # loss converting grid power to battery charge power
    'charge_loss': None,              # loss converting charge power to residual
    'inverter_power': None,           # Inverter power consumption W
    'bms_power': 27,                  # BMS power consumption W
    'bat_resistance': 0.072,          # internal resistance of a battery
    'volt_curve': lifepo4_curve,      # battery OCV range from 0% to 100% SoC
    'nominal_soc': 60,                # SoC for nominal open circuit battery voltage
    'generation_days': 3,             # number of days to use for average generation (1-7)
    'consumption_days': 3,            # number of days to use for average consumption (1-7)
    'consumption_span': 'week',       # 'week' = last n days or 'weekday' = last n weekdays
    'use_today': 21.0,                # hour when todays consumption and generation can be used
    'min_hours': 0.25,                # minimum charge time in decimal hours
    'min_kwh': 0.5,                   # minimum to add in kwh
    'solcast_adjust': 100,            # % adjustment to make to Solcast forecast
    'solar_adjust':  100,             # % adjustment to make to Solar forecast
    'forecast_selection': 1,          # 0 = use available forecast / generation, 1 only update settings with forecast
    'annual_consumption': None,       # optional annual consumption in kWh
    'timed_mode': 0,                  # 1 = apply timed work mode, 0 = None
    'special_contingency': 33,        # contingency for special days when consumption might be higher
    'special_days': ['12-25', '12-26', '01-01'],
    'full_charge': None,              # day of month (1-28) to do full charge, or 'daily' or 'Mon', 'Tue' etc
    'derate_temp': 22,                # battery temperature where cold derating starts to be applied
    'derate_step': 5,                 # scale for derating factors in C
    'derating': [24, 15, 10, 2],      # max charge current e.g. 5C step = 22C, 17C, 12C, 7C
    'force': 1                        # 0 = don't over-ride schedule, 1 = disable schedule
}

# work out the charge times to set using the parameters:
#  forecast: the kWh expected tomorrow. If none, forecast data is loaded from solcast etc
#  update_settings: 0 no updates, 1 update charge settings, 2 update work mode, 3 update both. The default is 0
#  show_data: 1 shows battery SoC, 2 shows battery residual. Default = 0
#  show_plot: 1 plots battery SoC, 2 plots battery residual. Default = 1
#  run_after: 0 over-rides 'forecast_times'. The default is 1.
#  forecast_times: list of hours when forecast can be fetched
#  force_charge: 1 = set force charge, 2 = charge for whole period

def charge_needed(forecast=None, update_settings=0, timed_mode=None, show_data=None, show_plot=None, run_after=None,
        forecast_times=None, force_charge=None, test_time=None, test_soc=None, test_charge=None, **settings):
    global device, seasonality, solcast_api_key, debug_setting, tariff, solar_arrays, legend_location, time_shift, battery_capacity, default_min_soc
    print(f"\n---------------- charge_needed ----------------")
    # validate parameters
    args = locals()
    s = ""
    for k in [k for k in args.keys() if args[k] is not None and k != 'settings']:
        s += f"\n  {k} = {args[k]}"
    # store settings:
    for key, value in settings.items():
        if key not in charge_config:
            print(f"** unknown configuration parameter: {key}")
        else:
            charge_config[key] = value
            s += f"\n  {key} = {value}"
    if len(s) > 0:
        print(f"Parameters: {s}")
    if tariff is not None:
        print(f"  tariff = {tariff['name']}")
    # set default parameters
    show_data = 1 if show_data is None or show_data == True else 0 if show_data == False else show_data
    show_plot = 3 if show_plot is None or show_plot == True else 0 if show_plot == False else show_plot
    run_after = 1 if run_after is None else run_after 
    timed_mode = 1 if timed_mode is None and tariff is not None and tariff.get('default_mode') is not None else 0 if timed_mode is None else timed_mode
    if forecast_times is None:
        forecast_times = tariff['forecast_times'] if tariff is not None and tariff.get('forecast_times') is not None else [22,23]
    if type(forecast_times) is not list:
        forecast_times = [forecast_times]
    # get dates and times
    system_time = (datetime.now(tz=timezone.utc) + timedelta(hours=time_shift)) if test_time is None else datetime.strptime(test_time, '%Y-%m-%d %H:%M')
    time_offset = daylight_saving(system_time) if daylight_saving is not None else 0
    now = system_time + timedelta(hours=time_offset)
    today = datetime.strftime(now, '%Y-%m-%d')
    base_hour = now.hour
    hour_now = now.hour + now.minute / 60
    print(f"  datetime = {today} {hours_time(hour_now)}")
    yesterday = datetime.strftime(now - timedelta(days=1), '%Y-%m-%d')
    tomorrow = datetime.strftime(now + timedelta(days=1), '%Y-%m-%d')
    day_tomorrow = day_names[(now.weekday() + 1) % 7]
    day_after_tomorrow = datetime.strftime(now + timedelta(days=2), '%Y-%m-%d')
    # work out if we lose 1 hour if clocks go forward or gain 1 hour if clocks go back
    change_hour = 0
    hour_adjustment = 0 if daylight_saving is None else daylight_changes(system_time, system_time + timedelta(days=2))
    if hour_adjustment != 0:    # change happens in the next 2 days - work out if today, tomorrow or day after tomorrow
        change_hour = 1 if daylight_changes(system_time, f"{tomorrow} 00:00") != 0 else 25 if daylight_changes(f"{tomorrow} 00:00", f"{day_after_tomorrow} 00:00") != 0 else 49
        change_hour += 1 if hour_adjustment > 0 else 0
    # get next charge times from am/pm charge times
    force_charge = 0 if force_charge is None else force_charge
    start_am = time_hours(tariff['off_peak1']['start'] if tariff is not None else 2.0)
    end_am = time_hours(tariff['off_peak1']['end'] if tariff is not None else 5.0)
    force_charge_am = 0 if tariff is not None and tariff['off_peak1']['force'] == 0 or force_charge == 0 else force_charge
    time_to_am = round_time(start_am - base_hour)
    start_pm = time_hours(tariff['off_peak2']['start'] if tariff is not None else 0.0)
    end_pm = time_hours(tariff['off_peak2']['end'] if tariff is not None else 0.0)
    force_charge_pm = 0 if tariff is not None and tariff['off_peak2']['force'] == 0 or force_charge == 0 else 1
    time_to_pm = round_time(start_pm - base_hour) if start_pm > 0 else None
    no_go1 = time_to_am is not None and hour_in(hour_now, {'start': round_time(start_am - 0.25), 'end': round_time(end_am + 0.25)})
    no_go2 = time_to_pm is not None and hour_in(hour_now, {'start': round_time(start_pm - 0.25), 'end': round_time(end_pm + 0.25)})
    if (no_go1 or no_go2) and update_settings > 0:
        print(f"\nInverter settings will not be changed less than 15 minutes before or after a charging period")
        update_settings = 0
    # choose and configure parameters for next charge time period
    charge_pm = time_to_pm is not None and time_to_pm < time_to_am
    force_charge = force_charge_pm if charge_pm else force_charge_am
    start_at = start_pm if charge_pm else start_am
    end_by = end_pm if charge_pm else end_am
    charge_time = round_time(end_by - start_at)
    time_to_start = time_to_pm if charge_pm else time_to_am
    start_hour = base_hour + time_to_start
    time_to_next = int(time_to_start)
    start_part_hour = time_to_start % 1
    if hour_adjustment < 0 and start_hour > change_hour:
        time_to_next -= 1       # 1 hour less if charging after clocks go forward
    run_time = int((time_to_am if charge_pm else time_to_am + 24 if time_to_pm is None else time_to_pm) + 0.99) + 1 + hour_adjustment
    # if we need to do a full charge, full_charge is the date, otherwise None
    full_charge = charge_config['full_charge'] if not charge_pm else None
    if type(full_charge) is int:            # value = day of month
        full_charge = tomorrow if full_charge is not None and int(tomorrow[-2:]) == full_charge else None
    elif type(full_charge) is str:          # value = daily or day of week
        full_charge = tomorrow if full_charge.lower() == 'daily' or full_charge.title() == day_tomorrow[:3] else None
    if debug_setting > 1:
        print(f"\ntoday = {today}, tomorrow = {tomorrow}, time_shift = {time_shift}")
        print(f"start_am = {start_am}, end_am = {end_am}, force_am = {force_charge_am}, time_to_am = {time_to_am}")
        print(f"start_pm = {start_pm}, end_pm = {end_pm}, force_pm = {force_charge_pm}, time_to_pm = {time_to_pm}")
        print(f"start_at = {start_at}, end_by = {end_by}, force_charge = {force_charge}")
        print(f"base_hour = {base_hour}, hour_adjustment = {hour_adjustment}, change_hour = {change_hour}")
        print(f"time_to_start = {time_to_start}, run_time = {run_time}, charge_pm = {charge_pm}")
        print(f"start_hour = {start_hour}, time_to_next = {time_to_next}, full_charge = {full_charge}")
    # get device and battery info from inverter
    if charge_config['capacity'] is not None and battery_capacity is None:
        battery_capacity = charge_config['capacity']
    if test_soc is None:
        if charge_config.get('min_soc') is not None:
            min_soc = charge_config['min_soc']
        else:
            get_min()
            if battery_settings.get('minGridSoc') is not None:
                min_soc = battery_settings['minGridSoc']
            else:
                print(f"\nMin SoC is not available. Please provide % via 'min_soc' parameter")
                return None
        get_battery()
        if battery['status'] != 1:
            print(f"\nBattery status is not available")
            return None
        current_soc = battery['soc']
        bat_volt = battery['volt']
        bat_power = battery['power']
        bat_current = battery['current']
        temperature = battery['temperature']
        residual = battery['residual']/1000
        if charge_config.get('capacity') is not None:
            capacity = charge_config['capacity']
        elif residual is not None and residual > 0.0 and current_soc is not None and current_soc > 0.0:
            capacity = residual * 100 / current_soc
        else:
            print(f"Battery capacity is not available. Please provide kWh via 'capacity' parameter")
            return None
    else:
        current_soc = test_soc
        capacity = 8.2
        residual = test_soc * capacity / 100
        min_soc = 10
        bat_volt = 122 / (1 + 0.03 * (100 - test_soc) / 90)
        bat_power = 0.0
        temperature = 19.5
        bat_current = 0.0
    volt_curve = charge_config['volt_curve']
    nominal_soc = charge_config['nominal_soc']
    volt_nominal = interpolate(nominal_soc / 10, volt_curve)
    bat_count = int(bat_volt / volt_nominal + 0.5)
    bat_resistance = charge_config['bat_resistance'] * bat_count
    bat_ocv = (bat_volt + bat_current * bat_resistance) * volt_nominal / interpolate(current_soc / 10, volt_curve)
    reserve = capacity * min_soc / 100
    available = residual - reserve
    print(f"\nBattery Info:")
    print(f"  Count:       {bat_count} batteries")
    print(f"  Capacity:    {capacity:.1f}kWh")
    print(f"  Residual:    {residual:.1f}kWh")
    print(f"  Voltage:     {bat_volt:.1f}V")
    print(f"  Current:     {bat_current:.1f}A")
    print(f"  State:       {'Charging' if bat_power < 0 else 'Discharging'} ({abs(bat_power):.3f}kW)")
    print(f"  Current SoC: {current_soc}% ({residual:.1f}kWh)")
    print(f"  Min SoC:     {min_soc}% ({reserve:.1f}kWh)")
    print(f"  Temperature: {temperature:.1f}°C")
    print(f"  Resistance:  {bat_resistance:.2f} ohms")
    print(f"  Nominal OCV: {bat_ocv:.1f}V at {nominal_soc}% SoC")
    # get power and charge current for device
    device_power = device.get('power')
    device_current = device.get('max_charge_current')
    if device_power is None or device_current is None:
        model = device.get('deviceType') if device.get('deviceType') is not None else 'deviceType?'
        print(f"** could not get parameters for {model}")
        device_power = 3.68
        device_current = 26
    # charge times are derated based on temperature
    charge_current = device_current if charge_config['charge_current'] is None else charge_config['charge_current']
    derate_temp = charge_config['derate_temp']
    if temperature > 36:
        print(f"\nHigh battery temperature may affect the charge rate")
    elif round(temperature, 0) <= derate_temp:
        print(f"\nLow battery temperature may affect the charge rate")
        derating = charge_config['derating']
        derate_step = charge_config['derate_step']
        i = int((derate_temp - temperature) / (derate_step if derate_step is not None and derate_step > 0 else 1))
        if derating is not None and type(derating) is list and i < len(derating):
            derated_current = derating[i]
            if derated_current < charge_current:
                print(f"  Charge current reduced from {charge_current:.0f}A to {derated_current:.0f}A" )
                charge_current = derated_current
        else:
            force_charge = 2
            print(f"  Full charge set")
    # work out charge limit = max power going into the battery after ac conversion losses
    charge_limit = device_power * charge_config['grid_loss']
    charge_power = charge_current * (bat_ocv + charge_current * bat_resistance) / 1000
    if charge_power < 0.1:
        print(f"** charge_current is too low ({charge_current:.1f}A)")
    elif charge_power < charge_limit:
        charge_limit = charge_power
    # work out losses when charging / force discharging
    inverter_power = charge_config['inverter_power'] if charge_config['inverter_power'] is not None else round(device_power, 0) * 20
    bms_power = charge_config['bms_power']
    charge_loss = charge_config.get('charge_loss')
    if charge_loss is None:
        charge_loss = 1.0 - charge_limit * 1000 * bat_resistance / bat_ocv ** 2 - bms_power / charge_limit / 1000
    operating_loss = inverter_power / 1000
    # work out discharge limit = max power coming from the battery before ac conversion losses
    discharge_loss = charge_config['discharge_loss']
    discharge_limit = device_power
    discharge_current = device_current if charge_config['discharge_current'] is None else charge_config['discharge_current']
    discharge_power = discharge_current * bat_ocv / 1000
    discharge_limit = discharge_power if discharge_power < discharge_limit else discharge_limit
    # charging happens if generation exceeds export limit in feedin work mode
    export_power = device_power if charge_config['export_limit'] is None else charge_config['export_limit']
    export_limit = export_power / discharge_loss
    if debug_setting > 1:
        print(f"\ncharge_config = {json.dumps(charge_config, indent=2)}")
    print(f"\nDevice Info:")
    print(f"  Rating:    {device_power:.2f}kW")
    print(f"  Export:    {export_power:.2f}kW")
    print(f"  Charge:    {charge_current:.1f}A, {charge_limit:.2f}kW, {charge_loss * 100:.1f}% efficient")
    print(f"  Discharge: {discharge_current:.1f}A, {discharge_limit:.2f}kW, {discharge_loss * 100:.1f}% efficient")
    print(f"  Inverter:  {inverter_power:.0f}W power consumption")
    print(f"  BMS:       {bms_power:.0f}W power consumption")
    # get consumption data
    annual_consumption = charge_config['annual_consumption']
    if annual_consumption is not None:
        consumption = annual_consumption / 365 * seasonality[now.month - 1] / sum(seasonality) * 12
        consumption_by_hour = daily_consumption
        print(f"\nEstimated consumption: {consumption:.1f}kWh")
    else:
        consumption_days = charge_config['consumption_days']
        consumption_days = 3 if consumption_days > 7 or consumption_days < 1 else consumption_days
        consumption_span = charge_config['consumption_span']
        if consumption_span == 'weekday':
            history = get_report('day', d=date_list(span='weekday', e=tomorrow, today=2)[-consumption_days-1:-1], v='loads')
        else:
            last_date = today if hour_now >= charge_config['use_today'] else yesterday
            history = get_report('day', d=date_list(span='week', e=last_date, today=1)[-consumption_days:], v='loads')
        (consumption, consumption_by_hour) = report_value_profile(history)
        if consumption is None:
            print(f"No consumption data available")
            return None
        print(f"\nConsumption (kWh):")
        s = ""
        for h in history:
            s += f"  {h['date']}: {h['total']:4.1f},"
        print(s[:-1])
        print(f"  Average of last {consumption_days} {day_tomorrow if consumption_span=='weekday' else 'day'}s: {consumption:.1f}kWh")
    # time line has 1 hour buckets of consumption
    daily_sum = sum(consumption_by_hour)
    consumption_timed = timed_list([consumption * x / daily_sum for x in consumption_by_hour], hour_now, run_time)
    # get Solcast data and produce time line
    solcast_value = None
    solcast_profile = None
    if forecast is None and solcast_api_key is not None and solcast_api_key != 'my.solcast_api_key' and (base_hour in forecast_times or run_after == 0):
        fsolcast = Solcast(quiet=True, estimated=0)
        if fsolcast is not None and hasattr(fsolcast, 'daily') and fsolcast.daily.get(tomorrow) is not None:
            (solcast_value, solcast_timed) = forecast_value_timed(fsolcast, today, tomorrow, hour_now, run_time, time_offset)
            print(f"\nSolcast forecast for {tomorrow}: {solcast_value:.1f}kWh")
            adjust = charge_config['solcast_adjust']
            if adjust != 100:
                solcast_value = solcast_value * adjust / 100
                solcast_timed = [v * adjust / 100 for v in solcast_timed]
                print(f"  Adjusted forecast: {solcast_value:.1f}kWh ({adjust}%)")
    # get forecast.solar data and produce time line
    solar_value = None
    solar_profile = None
    if forecast is None and solar_arrays is not None and (base_hour in forecast_times or run_after == 0):
        fsolar = Solar(quiet=True)
        if fsolar is not None and hasattr(fsolar, 'daily') and fsolar.daily.get(tomorrow) is not None:
            (solar_value, solar_timed) = forecast_value_timed(fsolar, today, tomorrow, hour_now, run_time)
            print(f"\nSolar forecast for {tomorrow}: {solar_value:.1f}kWh")
            adjust = charge_config['solar_adjust']
            if adjust != 100:
                solar_value = solar_value * adjust / 100
                solar_timed = [v * adjust / 100 for v in solar_timed]
                print(f"  Adjusted forecast: {solar_value:.1f}kWh ({adjust}%)")
    if solcast_value is None and solar_value is None and debug_setting > 1:
        print(f"\nNo forecasts available at this time")
    # get generation data
    generation = None
    last_date = today if hour_now >= charge_config['use_today'] else yesterday
    gen_days = charge_config['generation_days']
    history = get_history('week', d=last_date, v=['pvPower','meterPower2'], summary=2)
    pv_history = {}
    if history is not None and len(history) > 0:
        for day in history:
            date = day['date']
            if pv_history.get(date) is None:
                pv_history[date] = 0.0
            if day.get('kwh') is not None and day.get('kwh_neg') is not None:
                pv_history[date] += day['kwh_neg'] / 0.92 if day['variable'] == 'meterPower2' else day['kwh']
        pv_sum = sum([pv_history[d] for d in sorted(pv_history.keys())[-gen_days:]])
        print(f"\nGeneration (kWh):")
        s = ""
        for d in sorted(pv_history.keys())[-gen_days:]:
            s += f"  {d}: {pv_history[d]:4.1f},"
        print(s[:-1])
        generation = pv_sum / gen_days
        print(f"  Average of last {gen_days} days: {generation:.1f}kWh")
    # choose expected value and produce generation time line
    quarter = now.month // 3 % 4
    sun_name = seasonal_sun[quarter]['name']
    sun_profile = seasonal_sun[quarter]['sun']
    sun_sum = sum(sun_profile)
    sun_timed = timed_list(sun_profile, hour_now, run_time)
    if forecast is not None:
        expected = forecast
        generation_timed = [expected * x / sun_sum for x in sun_timed]
        print(f"\nUsing manual forecast for {tomorrow}: {expected:.1f}kWh with {sun_name} sun profile")
    elif solcast_value is not None:
        expected = solcast_value
        generation_timed = solcast_timed
        print(f"\nUsing Solcast forecast for {tomorrow}: {expected:.1f}kWh")
    elif solar_value is not None:
        expected = solar_value
        generation_timed = solar_timed
        print(f"\nUsing Solar forecast for {tomorrow}: {expected:.1f}kWh")
    elif generation is None or generation == 0.0:
        print(f"\nNo generation data available")
        return None
    else:
        expected = generation
        generation_timed = [expected * x / sun_sum for x in sun_timed]
        print(f"\nUsing generation of {expected:.1f}kWh with {sun_name} sun profile")
        if charge_config['forecast_selection'] == 1 and update_settings > 0:
            print(f"  Settings will not be updated when forecast is not available")
            update_settings = 2 if update_settings == 3 else 0
    # produce time lines for main charge and discharge (after losses)
    charge_timed = [x * charge_config['pv_loss'] for x in generation_timed]
    discharge_timed = [x / charge_config['discharge_loss'] + charge_config['bms_power'] / 1000 for x in consumption_timed]
    # adjust charge and discharge time lines for work mode, force charge and power limits
    for i in range(0, run_time):
        h = base_hour + i
        # cap charge / discharge power
        charge_timed[i] = charge_limit if charge_timed[i] > charge_limit else charge_timed[i]
        discharge_timed[i] = discharge_limit if discharge_timed[i] > discharge_limit else discharge_timed[i]
        if force_charge_am == 1 and hour_in(h, {'start': start_am, 'end': end_am}):
            discharge_timed[i] = operating_loss if charge_timed[i] == 0.0 else 0.0
        elif force_charge_pm == 1 and hour_in(h, {'start': start_pm, 'end': end_pm}):
            discharge_timed[i] = operating_loss if charge_timed[i] == 0.0 else 0.0
        elif timed_mode > 0 and tariff is not None and hour_in(h, tariff.get('Backup')):
            discharge_timed[i] = operating_loss if charge_timed[i] == 0.0 else 0.0
        elif timed_mode > 0 and tariff is not None and hour_in(h, tariff.get('Feedin')):
            (discharge_timed[i], charge_timed[i]) = (0.0 if (charge_timed[i] >= discharge_timed[i]) else (discharge_timed[i] - charge_timed[i]),
                0.0 if (charge_timed[i] <= export_limit + discharge_timed[i]) else (charge_timed[i] - export_limit - discharge_timed[i]))
    # track the battery residual over the run time (if we don't add any charge)
    # adjust residual from hour_now to what it was at the start of current hour
    h = base_hour
    kwh_timed = [charge * charge_loss - discharge for charge, discharge in zip(charge_timed, discharge_timed)]
    kwh_current = residual - kwh_timed[0] * (hour_now - h)
    bat_timed = []
    kwh_min = kwh_current
    min_hour = h
    reserve_drain = reserve
    for i in range(0, run_time):
        if kwh_current <= reserve and i <= time_to_next:
            # battery is empty
            if reserve_drain < (capacity * 6 / 100):
                reserve_drain = reserve           # force charge to restore reserve
            kwh_current = reserve_drain           # stop discharge below reserve
            reserve_drain -= bms_power / 1000     # allow for BMS drain
        else:
            reserve_drain = reserve                # reset drain
        if kwh_current > capacity:
            # battery is full
            kwh_current = capacity
        bat_timed.append(kwh_current)
        if kwh_current < kwh_min:       # track minimum and time
            kwh_min = kwh_current
            min_hour = h
        kwh_current += kwh_timed[i]
        h += 1
    # work out what we need to add to stay above reserve and provide contingency
    contingency = charge_config['special_contingency'] if tomorrow[-5:] in charge_config['special_days'] and not charge_pm else charge_config['contingency']
    kwh_contingency = consumption * contingency / 100
    kwh_needed = reserve + kwh_contingency - kwh_min
    day_when = 'today' if min_hour < 24 else 'tomorrow' if min_hour <= 48 else 'day after tomorrow'
    start_residual = interpolate(time_to_start, bat_timed)      # residual when charging starts
    if kwh_min > reserve and kwh_needed < charge_config['min_kwh'] and full_charge is None and test_charge is None:
        print(f"\nNo charging is needed, lowest forecast SoC = {kwh_min / capacity * 100:3.0f}% (Residual = {kwh_min:.2f}kWh)")
        print(f"  Contingency of {kwh_contingency:.2f}kWh ({contingency}%) is available at {hours_time(min_hour)} {day_when}")
        charge_message = "no charge needed"
        kwh_needed = 0.0
        hours = 0.0
        end1 = start_at
    else:
        charge_message = "with charge added"
        if test_charge is None:
            min_hour_adjust = min_hour - hour_adjustment if min_hour >= change_hour else 0
            print(f"\nCharge of {kwh_needed:.2f} kWh is needed for a contingency of {kwh_contingency:.2f} kWh ({contingency}%) at {hours_time(min_hour_adjust)} {day_when}")
        else:
            print(f"\nTest charge of {test_charge}kWh")
            charge_message = "** test charge **"
            kwh_needed = test_charge
        # work out time to add kwh_needed to battery
        taper_time = 10/60 if (start_residual + kwh_needed) >= (capacity * 0.95) else 0
        hours = round_time(kwh_needed / (charge_limit * charge_loss + discharge_timed[time_to_next]) + taper_time)
        # full charge if requested or charge time exceeded or charge needed exceeds capacity
        if full_charge is not None or force_charge == 2 or hours > charge_time or (start_residual + kwh_needed) > (capacity * 1.05):
            kwh_needed = capacity - start_residual
            hours = charge_time
            print(f"  Full charge time used, expecting to add {kwh_needed:.2f} kWh")
        elif hours < charge_config['min_hours']:
            hours = charge_config['min_hours']
            print(f"  Minimum charge time used")
        end1 = round_time(start_at + hours)
        # rework charge and discharge and work out grid consumption
        start_timed = time_to_start      # relative start and end time 
        end_timed = start_timed + hours
        charge_timed_old = [x for x in charge_timed]
        discharge_timed_old = [x for x in discharge_timed]
        for i in range(time_to_next, int(time_to_next + hours + 2)):
            j = i + 1
            # work out time (fraction of hour) when charging in hour from i to j
            if start_timed >= i and end_timed < j:
                t = end_timed - start_timed         # start and end in same hour
            elif start_timed >= i and start_timed < j and end_timed >= j:
                t = j - start_timed                 # start this hour but not end
            elif end_timed > i and end_timed <= j and start_timed <= i:
                t = end_timed - i                   # end this hour but not start
            elif start_timed <= i and end_timed > j:
                t = 1.0                             # complete hour inside start and end
            else:
                t = 0.0                             # complete hour before start or after end
            if debug_setting > 1:
                print(f"i = {i}, j = {j}, t = {t}")
            charge_added = charge_limit * t
            charge_timed[i] = charge_timed[i] + charge_added if charge_timed[i] + charge_added < charge_limit else charge_limit
            discharge_timed[i] *= (1-t)
        # rebuild the battery residual with the charge added
        # adjust residual from hour_now to what it was at the start of current hour
        h = base_hour
        kwh_timed = [charge * charge_loss - discharge for charge, discharge in zip(charge_timed, discharge_timed)]
        kwh_current = residual - kwh_timed[0] * (hour_now - h)
        bat_timed_old = [x for x in bat_timed]     # save for before / after comparison
        bat_timed = []
        reserve_drain = reserve
        for i in range(0, run_time):
            if kwh_current <= reserve:
                # battery is empty
                if reserve_drain < (capacity * 6 / 100):
                    reserve_drain = reserve           # force charge by BMS
                kwh_current = reserve_drain           # stop discharge below reserve
                reserve_drain -= bms_power / 1000     # BMS drain
            else:
                reserve_drain = reserve                # reset drain
            if kwh_current > capacity:
                # battery is full
                kwh_current = capacity
            bat_timed.append(kwh_current)
            kwh_current += kwh_timed[i]
            h += 1
        time_to_end = int(start_timed + hours) + 1
        kwh_added = bat_timed[time_to_end] - bat_timed_old[time_to_end]
        end_part_hour = end_timed - int(end_timed)
        old_residual = interpolate(end_timed, bat_timed_old)
        new_residual = capacity if old_residual + kwh_added > capacity else old_residual + kwh_added
        net_added = new_residual - start_residual
        print(f"\nCharging for {int(hours * 60)} minutes adds {net_added:.2f}kWh")
#        print(f"  Charging for {int(hours * 60)} minutes adds {kwh_added:.2f}kWh ({net_added:.2f}kWh net)")
        print(f"  Start SoC: {start_residual / capacity * 100:3.0f}% at {hours_time(start_at)} ({start_residual:.2f}kWh)")
#        print(f"  Old SoC:   {old_residual / capacity * 100:3.0f}% at {hours_time(end1)} ({old_residual:.2f}kWh)")
        print(f"  End SoC:   {new_residual / capacity * 100:3.0f}% at {hours_time(end1)} ({new_residual:.2f}kWh)")
    if show_data > 2:
        print(f"\nTime, Generation, Charge, Consumption, Discharge, Residual, kWh")
        for i in range(0, run_time):
            h = base_hour + i
            print(f"  {hours_time(h)}, {generation_timed[i]:6.3f}, {charge_timed[i]:6.3f}, {consumption_timed[i]:6.3f}, {discharge_timed[i]:6.3f}, {bat_timed[i]:6.3f}")
        if kwh_needed > 0 and show_data > 3:
            print(f"\nTime, Generation, Charge, Consumption, Discharge, Residual, kWh (before charging)")
            for i in range(0, run_time):
                h = base_hour + i
                print(f"  {hours_time(h)}, {generation_timed[i]:6.3f}, {charge_timed_old[i]:6.3f}, {consumption_timed[i]:6.3f}, {discharge_timed_old[i]:6.3f}, {bat_timed_old[i]:6.3f}")
    if show_data > 0:
        s = f"\nBattery Energy kWh ({charge_message}):\n" if show_data == 2 else f"\nBattery SoC % ({charge_message}):\n"
        s += " " * (18 if show_data == 2 else 17) * (base_hour % 6)
        h = base_hour
        for r in bat_timed:
            s += "\n" if h > hour_now and h % 6 == 0 else ""
            s += f"  {hours_time(h - (hour_adjustment if h >= change_hour else 0), day=True)}"
            s += f" = {r:5.2f}," if show_data == 2 else f" = {r / capacity * 100:3.0f}%,"
            h += 1
        print(s[:-1])
    if show_plot > 0:
        print()
        plt.figure(figsize=(figure_width, figure_width/2))
        x_timed = [i for i in range(0, run_time)]
        plt.xticks(ticks=x_timed, labels=[hours_time(base_hour + x - (hour_adjustment if (base_hour + x) >= change_hour else 0), day=False) for x in x_timed], rotation=90, fontsize=8, ha='center')
        if show_plot == 1:
            title = f"Battery SoC % ({charge_message})"
            plt.plot(x_timed, [round(bat_timed[x] * 100 / capacity,1) for x in x_timed], label='Battery', color='blue')
        else:
            title = f"Energy Flow kWh ({charge_message})"
            plt.plot(x_timed, bat_timed, label='Battery', color='blue')
            plt.plot(x_timed, generation_timed, label='Generation', color='green')
            plt.plot(x_timed, consumption_timed, label='Consumption', color='red')
#            if kwh_needed > 0:
#                plt.plot(x_timed, bat_timed_old, label='Battery (before charging)', color='blue', linestyle='dotted')
            if show_plot == 3:
                plt.plot(x_timed, charge_timed, label='Charge', color='orange', linestyle='dotted')
                plt.plot(x_timed, discharge_timed, label='Discharge', color='brown', linestyle='dotted')
        plt.title(title, fontsize=12)
        plt.grid()
        if show_plot > 1:
            plt.legend(fontsize=8, loc="upper right")
        plt.show()
    if test_charge is not None:
        return None
    # work out charge periods settings
    start2 = round_time(start_at if hours == 0 else end1 + 1 / 60)       # add 1 minute to end time
    if force_charge == 1 and hour_in(start2, {'start':start_at, 'end': end_by}):
        end2 = end_by
    else:
            end2 = start2
    # setup charging
    if update_settings in [1,3]:
        # adjust times for clock changes
        adjust = hour_adjustment if hour_adjustment != 0 and start_hour > change_hour else 0

        set_charge(ch1 = True, st1 = start_at, en1 = end1, ch2 = False, st2 = start2, en2 = end2, adjust = adjust, force = charge_config['force'])
    else:
        print(f"\nNo changes made to charge settings")
    # timed work mode change
    target_mode = tariff.get('default_mode') if tariff is not None else None
    if update_settings in [0,1] or target_mode is None:
        return None
    required_soc = 0
    current_mode = get_work_mode()
    if current_mode is None:
        return None
    for w in work_modes:
        if tariff.get(w) is not None and hour_in(hour_now, tariff[w]):
                target_mode = w
                required_soc = tariff[w].get('min_soc') if tariff[w].get('min_soc') is not None else 0
    if update_settings in [2,3] and current_mode != target_mode:
        print(f"\nCurrent SoC = {current_soc}%, Required SoC = {required_soc}%, Target work mode = '{target_mode}'")
        if current_soc >= required_soc and set_work_mode(target_mode) == target_mode:
            print(f"  Changed work mode from '{current_mode}' to '{target_mode}'")
    else:
        print(f"\nCurrent work mode is '{current_mode}'")
    return None

##################################################################################################
# Date Ranges
##################################################################################################

# generate a list of dates, where the last date is not later than yesterday or today
# s and e: start and end dates using the format 'YYYY-MM-DD'
# limit: limits the total number of days (default is 200)
# today: 1 defaults the date to today as the last date, otherwise, yesterday
# span: 'week', 'month' or 'year' generated dates that span a week, month or year
# quiet: do not print results if True

def date_list(s = None, e = None, limit = None, span = None, today = 0, quiet = True):
    global debug_setting
    latest_date = datetime.date(datetime.now())
    today = 0 if today == False else 1 if today == True else today
    if today == 0:
        latest_date -= timedelta(days=1)
    first = datetime.date(datetime.strptime(s, '%Y-%m-%d')) if type(s) is str else s.date() if s is not None else None
    last = datetime.date(datetime.strptime(e, '%Y-%m-%d')) if type(e) is str else e.date() if e is not None else None
    last = latest_date if last is not None and last > latest_date and today != 2 else last
    step = 1
    if first is None and last is None:
        last = latest_date
    if span is not None:
        span = span.lower()
        limit = 366 if limit is None else limit
        if span == 'day':
            limit = 1
        elif span == '2days':
            # e.g. yesterday and today
            last = first + timedelta(days=1) if first is not None else last
            first = last - timedelta(days=1) if first is None else first
        elif span == 'weekday':
            # e.g. last 8 days with same day of the week
            last = first + timedelta(days=49) if first is not None else last
            first = last - timedelta(days=49) if first is None else first
            step = 7
        elif span == 'week':
            # number of days in a week less 1 day
            last = first + timedelta(days=6) if first is not None else last
            first = last - timedelta(days=6) if first is None else first
        elif span == 'month':
            if first is not None:
                # number of days in this month less 1 day
                days = ((first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day - 1
            else:
                # number of days in previous month less 1 day
                days = (last.replace(day=1) - timedelta(days=1)).day - 1
            last = first + timedelta(days=days) if first is not None else last
            first = last - timedelta(days=days) if first is None else first
        elif span == 'year':
            if first is not None:
                # number of days in coming year
                days = (first.replace(year=first.year+1,day=28 if first.month==2 and first.day==29 else first.day) - first).days - 1
            else:
                # number of days in previous year
                days = (last - last.replace(year=last.year-1,day=28 if last.month==2 and last.day==29 else last.day)).days - 1
            last = first + timedelta(days=days) if first is not None else last
            first = last - timedelta(days=days) if first is None else first
        else:
            print(f"** span '{span}' was not recognised")
            return None
    else:
        limit = 200 if limit is None or limit < 1 else limit
    last = latest_date if last is None or (last > latest_date and today != 2) else last
    d = latest_date if first is None or (first > latest_date and today != 2) else first
    if d > last:
        d, last = last, d
    l = [datetime.strftime(d, '%Y-%m-%d')]
    while d < last  and len(l) < limit:
        d += timedelta(days=step)
        l.append(datetime.strftime(d, '%Y-%m-%d'))
    return l


##################################################################################################
##################################################################################################
# PV Output Section
##################################################################################################
##################################################################################################

# validate pv power data by checking it does not exceed a limit. Used to cover the situation where
# Fox returns PV Voltage instead of PV Power. For example, over 100v instead of under 100kW.
max_pv_power = 100
# data calibration settings compared with HA inverter data 
pv_calibration = 0.98
ct2_calibration = 0.92

##################################################################################################
# get PV Output upload data from the Fox Cloud as energy values for a list of dates
##################################################################################################

# get pvoutput data for upload to pvoutput api or via Bulk Loader
# tou: 0 = no time of use, 1 = use time of use periods if available

def get_pvoutput(d = None, tou = 0):
    global tariff, pv_calibration, ct2_calibration
    if d is None:
        d = date_list()[0]
    tou = 0 if tariff is None else 1 if tou == 1 or tou == True else 0
    if type(d) is list:
        print(f"---------------- get_pvoutput ------------------")
        print(f"Date range {d[0]} to {d[-1]} has {len(d)} days")
        if tou == 1:
            print(f"Time of use: {tariff['name']}")
        print(f"------------------------------------------------")
        for x in d:
            csv = get_pvoutput(x, tou)
            if csv is None:
                return None
            print(csv)
        return
    # get quick report of totals for the day
    v = ['loads'] if tou == 1 else ['loads', 'feedin', 'gridConsumption']
    report_data = get_report('day', d=d, v=v, summary=2)
    if report_data is None:
        return None
    # get raw power data for the day
    v = ['pvPower', 'meterPower2', 'feedinPower', 'gridConsumptionPower'] if tou == 1 else ['pvPower', 'meterPower2']
    raw_data = get_history('day', d=d + ' 00:00:00', v=v , summary=1)
    if raw_data is None or len(raw_data) == 0 or raw_data[0].get('kwh') is None or raw_data[0].get('max') is None:
        return(f"# error: {d.replace('-','')} No generation data available")
    # apply calibration and merge raw_data for meterPower2 into pvPower:
    pv_index = v.index('pvPower')
    ct2_index = v.index('meterPower2')
    for i, data in enumerate(raw_data[ct2_index]['data']):
        # meterPower2 is -ve when generating
        raw_data[pv_index]['data'][i]['value'] -= data['value'] / ct2_calibration if data['value'] <= 0.0 else 0
    # kwh is positive for generation
    raw_data[pv_index]['kwh'] = raw_data[pv_index]['kwh'] / pv_calibration + raw_data[ct2_index]['kwh_neg'] / ct2_calibration
    pv_max = max(data['value'] for data in raw_data[pv_index]['data'])
    max_index = [data['value'] for data in raw_data[pv_index]['data']].index(pv_max)
    raw_data[pv_index]['max'] = pv_max
    raw_data[pv_index]['max_time'] = raw_data[pv_index]['data'][max_index]['time'][11:16]
    # validation check: max_pv_power against max pvPower (including meterPower2)
    if pv_max > max_pv_power:
        return(f"# error: {d.replace('-','')} PV power ({pv_max}kWh) exceeds max_pv_power ({max_pv_power}kWh)")
    # generate output
    generate = ''
    export = ','
    power = ',,'
    export_tou = ',,,'
    consume = ','
    grid = ',,,,'
    for var in raw_data:     # process list of raw_data values (with TOU)
        wh = int(var['kwh'] * 1000)
        peak = int(var['kwh_peak'] * 1000)
        off_peak = int(var['kwh_off'] * 1000)
        if var['variable'] == 'pvPower':
            generation = wh
            date = var['date'].replace('-','')
            generate = f"{date},{generation},"
            power = f"{int(var['max'] * 1000)},{var['max_time']},"
        elif var['variable'] == 'feedinPower':
            export = f"{wh}," if tou == 0 else f","
            export_tou = f",,," if tou == 0 else f"{peak},{off_peak},{wh - peak - off_peak},0"
        elif var['variable'] == 'loadsPower':
            consume = f"{wh},"
        elif var['variable'] == 'gridConsumptionPower':
            grid = f"0,0,{wh},0," if tou == 0 else f"{peak},{off_peak},{wh - peak - off_peak},0,"
    for var in report_data:     # process list of report_data values (no TOU)
        wh = int(var['total'] * 1000)
        if var['variable'] == 'feedin':
            # check exported is less than generated
            if wh > generation:
                print(f"# warning: {date} Exported {wh}Wh is more than Generation")
                wh = generation
            export = f"{wh},"
            export_tou = f",,,"
        elif var['variable'] == 'loads':
            consume = f"{wh},"
        elif var['variable'] == 'gridConsumption':
            grid = f"0,0,{wh},0,"
    if generate == '':
        return None
    csv = generate + export + power + ',,,,' + grid + consume + export_tou
    return csv

pv_url = "https://pvoutput.org/service/r2/addoutput.jsp"
pv_api_key = None
pv_system_id = None

# upload data for a day using pvoutput api
def set_pvoutput(d = None, tou = 0):
    global pv_url, pv_api_key, pv_system_id, tariff
    if pv_api_key is None or pv_system_id is None or pv_api_key == 'my.pv_api_key' or pv_system_id == 'my.pv_system_id':
        print(f"** set_pvoutput: 'pv_api_key' / 'pv_system_id' not configured")
        return None
    if d is None:
        d = date_list(span='2days', today = 1)
    tou = 0 if tariff is None else 1 if tou == 1 or tou == True else 0
    if type(d) is list:
        print(f"\n--------------- set_pvoutput -----------------")
        print(f"Date range {d[0]} to {d[-1]} has {len(d)} days")
        if tou == 1 :
            print(f"Time of use: {tariff['name']}\n")
        print(f"------------------------------------------------")
        for x in d[:10]:
            csv = set_pvoutput(x, tou)
            if csv is None:
                return None
            print(f"{csv}  # uploaded OK")
        return
    headers = {'X-Pvoutput-Apikey': pv_api_key, 'X-Pvoutput-SystemId': pv_system_id, 'Content-Type': 'application/x-www-form-urlencoded'}
    csv = get_pvoutput(d, tou)
    if csv is None:
        return None
    if csv[0] == '#':
        return csv
    try:
        response = requests.post(url=pv_url, headers=headers, data='data=' + csv)
    except Exception as e:
        print(f"** unable to upload data to pvoutput.org, {e}. Please try again later")
        return None
    result = response.status_code
    if result != 200:
        if result == 401:
            print(f"** access denied for pvoutput.org. Check 'pv_api_key' and 'pv_system_id' are correct")
            return None
        print(f"** set_pvoutput got response code: {result}")
        return None
    return csv


##################################################################################################
##################################################################################################
# Solcast Section
##################################################################################################
##################################################################################################

def c_int(i):
    # handle None in integer conversion
    if i is None :
        return None
    return int(i)

def c_float(n):
    # handle None in float conversion
    if n is None :
        return float(0)
    return float(n)

##################################################################################################
# Code for loading and displaying yield forecasts from Solcast.com.au.
##################################################################################################

# solcast settings
solcast_url = 'https://api.solcast.com.au/'
solcast_api_key = None
solcast_rids = []       # no longer used, rids loaded from solcast.com
solcast_save = 'solcast.txt'
page_width = 100        # maximum text string for display

class Solcast :
    """
    Load Solcast Estimate / Actuals / Forecast daily yield
    """ 

    def __init__(self, days = 7, reload = 2, quiet = False, estimated=0) :
        # days sets the number of days to get for forecasts (and estimated if enabled)
        # reload: 0 = use solcast.json, 1 = load new forecast, 2 = use solcast.json if date matches
        # The forecasts and estimated both include the current date, so the total number of days covered is 2 * days - 1.
        # The forecasts and estimated also both include the current time, so the data has to be de-duplicated to get an accurate total for a day
        global debug_setting, solcast_url, solcast_api_key, solcast_save
        data_sets = ['forecasts']
        if estimated == 1:
            data_sets += ['estimated_actuals']
        self.data = {}
        self.today = datetime.strftime(datetime.date(datetime.now()), '%Y-%m-%d')
        self.tomorrow = datetime.strftime(datetime.date(datetime.now() + timedelta(days=1)), '%Y-%m-%d')
        self.save = solcast_save #.replace('.', '_%.'.replace('%', self.today.replace('-','')))
        if reload == 1 and os.path.exists(self.save):
            os.remove(self.save)
        if self.save is not None and os.path.exists(self.save):
            file = open(self.save)
            self.data = json.load(file)
            file.close()
            if len(self.data) == 0:
                print(f"No data in {self.save}")
            elif reload == 2 and 'date' in self.data and self.data['date'] != self.today:
                self.data = {}
            elif debug_setting > 0 and not quiet:
                print(f"Using data for {self.data['date']} from {self.save}")
                if self.data.get('estimated_actuals') is None:
                    data_sets = ['forecasts']
                    estimated = 0
        if len(self.data) == 0 :
            if solcast_api_key is None or solcast_api_key == 'my.solcast_api_key>':
                print(f"\nSolcast: solcast_api_key not set, exiting")
                return
            self.credentials = HTTPBasicAuth(solcast_api_key, '')
            if debug_setting > 1 and not quiet:
                print(f"Getting rids from solcast.com")
            params = {'format' : 'json'}
            response = requests.get(solcast_url + 'rooftop_sites', auth = self.credentials, params = params)
            if response.status_code != 200:
                if response.status_code == 429:
                    print(f"\nSolcast API call limit reached for today")
                else:
                    print(f"Solcast: response code getting rooftop_sites was {response.status_code}")
                return
            sites = response.json().get('sites')
            if debug_setting > 0 and not quiet:
                print(f"Getting forecast for {self.today} from solcast.com")
            self.data['date'] = self.today
            params = {'format' : 'json', 'hours' : 168, 'period' : 'PT30M'}     # always get 168 x 30 min values
            for t in data_sets :
                self.data[t] = {}
                for rid in [s['resource_id'] for s in sites] :
                    response = requests.get(solcast_url + 'rooftop_sites/' + rid + '/' + t, auth = self.credentials, params = params)
                    if response.status_code != 200 :
                        if response.status_code == 429:
                            print(f"\nSolcast: API call limit reached for today")
                        else:
                            print(f"Solcast: response code getting {t} was {response.status_code}: {response.reason}")
                        return
                    self.data[t][rid] = response.json().get(t)
            if self.save is not None :
                file = open(self.save, 'w')
                json.dump(self.data, file, sort_keys = True, indent=4, ensure_ascii= False)
                file.close()
        self.daily = {}
        for t in data_sets :
            for rid in self.data[t].keys() :            # aggregate sites
                if self.data[t][rid] is not None :
                    for f in self.data[t][rid] :            # aggregate 30 minute slots for each day
                        period_end = f.get('period_end')
                        date = period_end[:10]
                        hour = int(period_end[11:13])
                        if date not in self.daily.keys() :
                            self.daily[date] = {'hourly': {}, 'kwh': 0.0}
                        if hour not in self.daily[date]['hourly'].keys():
                            self.daily[date]['hourly'][hour] = 0.0
                        value = c_float(f.get('pv_estimate')) / 2                   # 30 minute power kw, yield / 2 = kwh
                        self.daily[date]['kwh'] += value
                        self.daily[date]['hourly'][hour] += value
        # ignore first and last dates as these only cover part of the day, so are not accurate
        self.keys = sorted(self.daily.keys())[1:-1]
        self.days = len(self.keys)
        # trim the range if fewer days have been requested
        while self.days > 2 * days :
            self.keys = self.keys[1:-1]
            self.days = len(self.keys)
        self.values = [self.daily[d]['kwh'] for d in self.keys]
        self.total = sum(self.values)
        if self.days > 0 :
            self.avg = self.total / self.days
        return

    def __str__(self) :
        # return printable Solcast info
        global debug_setting
        if not hasattr(self, 'days'):
            return 'Solcast: no days in forecast'
        s = f'\nSolcast forecast for {self.days} days'
        for d in self.keys :
            y = self.daily[d]['kwh']
            day = datetime.strptime(d, '%Y-%m-%d').strftime('%A')[:3]
            s += f"\n   {d} {day}: {y:5.2f}kWh"
        return s

    def plot_daily(self) :
        global figure_width, legend_location
        if not hasattr(self, 'daily') :
            print(f"Solcast: no daily data to plot")
            return
        plt.figure(figsize=(figure_width, figure_width/3))
        print()
        # plot estimated
        x = [f"{d} {datetime.strptime(d, '%Y-%m-%d').strftime('%A')[:3]} " for d in self.keys if d <= self.today]
        y = [self.daily[d]['kwh'] for d in self.keys if d <= self.today]
        if x is not None and len(x) != 0 :
            plt.bar(x, y, color='green', linestyle='solid', label='estimated', linewidth=2)
        # plot forecasts
        x = [f"{d} {datetime.strptime(d, '%Y-%m-%d').strftime('%A')[:3]} " for d in self.keys if d > self.today]
        y = [self.daily[d]['kwh'] for d in self.keys if d > self.today]
        if x is not None and len(x) != 0 :
            plt.bar(x, y, color='orange', linestyle='solid', label='forecast', linewidth=2)
        # annotations
        if hasattr(self, 'avg'):
            plt.axhline(self.avg, color='blue', linestyle='solid', label=f'average {self.avg:.1f} kwh / day', linewidth=2)
        title = f"Solcast daily yield on {self.today} for {self.days} days"
        title += f". Total yield = {self.total:.0f} kwh, Average = {self.avg:.0f}"    
        plt.title(title, fontsize=12)
        plt.grid()
#        plt.legend(fontsize=14, loc=legend_location)
        plt.xticks(rotation=45, ha='right')
        plt.show()
        return

    def plot_hourly(self, day = None) :
        if not hasattr(self, 'daily') :
            print(f"Solcast: no daily data to plot")
            return
        if day == 'today':
            day = self.today
        elif day == 'tomorrow':
            day = self.tomorrow
        elif day == 'all':
            day = self.keys
        elif day is None:
            day = [self.today, self.tomorrow]
        if type(day) is list:
            for d in day:
                self.plot_hourly(d)
            return
        plt.figure(figsize=(figure_width, figure_width/3))
        print()
        if day is None:
            day = self.tomorrow
        # plot forecasts
        hours = sorted([h for h in self.daily[day]['hourly'].keys()])
        x = [hours_time(h) for h in hours]
        y = [self.daily[day]['hourly'][h] for h in hours]
        color = 'orange' if day > self.today else 'green'
        if x is not None and len(x) != 0 :
            plt.plot(x, y, color=color, linestyle='solid', linewidth=2)
        title = f"Solcast hourly yield on {day}"
        title += f". Total yield = {self.daily[day]['kwh']:.1f}kwh"    
        plt.title(title, fontsize=12)
        plt.grid()
        plt.xticks(rotation=45, ha='right')
        plt.show()
        return



##################################################################################################
##################################################################################################
# Forecast.Solar Section
##################################################################################################
##################################################################################################


# forecast.solar global settings
solar_api_key = None
solar_url = "https://api.forecast.solar/"
solar_save = "solar.txt"
solar_arrays = None

# configure a solar_array
def solar_array(name = None, lat=51.1790, lon=-1.8262, dec = 30, az = 0, kwp = 5.0, dam = None, inv = None, hor = None):
    global solar_arrays
    if name is None:
        return
    if solar_arrays is None:
        solar_arrays = {}
    if name not in solar_arrays.keys():
        solar_arrays[name] = {}
    solar_arrays[name]['lat'] = round(lat,4)
    solar_arrays[name]['lon'] = round(lon,4)
    solar_arrays[name]['dec'] = 30 if dec < 0 or dec > 90 else dec
    solar_arrays[name]['az'] = az - 360 if az > 180 else az
    solar_arrays[name]['kwp'] = kwp
    solar_arrays[name]['dam'] = dam
    solar_arrays[name]['inv'] = inv
    solar_arrays[name]['hor'] = hor
    return


class Solar :
    """
    load forecast.solar info using solar_arrays
    """ 

    # get solar forecast and return total expected yield
    def __init__(self, reload=0, quiet=False):
        global solar_arrays, solar_save, solar_total, solar_url, solar_api_key
        self.today = datetime.strftime(datetime.date(datetime.now()), '%Y-%m-%d')
        self.tomorrow = datetime.strftime(datetime.date(datetime.now() + timedelta(days=1)), '%Y-%m-%d')
        self.arrays = None
        self.results = None
        self.save = solar_save #.replace('.', '_%.'.replace('%',self.today.replace('-','')))
        if reload == 1 and os.path.exists(self.save):
            os.remove(self.save)
        if self.save is not None and os.path.exists(self.save):
            file = open(self.save)
            data = json.load(file)
            file.close()
            if data.get('date') is not None and (data['date'] == self.today and reload != 1):
                if debug_setting > 0 and not quiet:
                    print(f"Using data for {data['date']} from {self.save}")
                self.results = data['results'] if data.get('results') is not None else None
                self.arrays = data['arrays'] if data.get('arrays') is not None else None
        if self.arrays is None or self.results is None:
            if solar_arrays is None or len(solar_arrays) < 1:
                print(f"** Solar: you need to add an array using solar_array()")
                return
            self.api_key = solar_api_key + '/' if solar_api_key is not None else ''
            self.arrays = deepcopy(solar_arrays)
            self.results = {}
            for name, a in self.arrays.items():
                if debug_setting > 0 and not quiet:
                    print(f"Getting data for {name} array")
                path = f"{a['lat']}/{a['lon']}/{a['dec']}/{a['az']}/{a['kwp']}"
                params = {'start': '00:00', 'no_sun': 1, 'damping': a['dam'], 'inverter': a['inv'], 'horizon': a['hor']}
                response = requests.get(solar_url + self.api_key + 'estimate/' + path, params = params)
                if response.status_code != 200:
                    if response.status_code == 429:
                        print(f"\nSolar: forecast.solar API call limit reached for today")
                    else:
                        print(f"** Solar() got response code {response.status_code}: {response.reason}")
                    return
                self.results[name] = response.json().get('result')
            if self.save is not None :
                if debug_setting > 0 and not quiet:
                    print(f"Saving data to {self.save}")
                file = open(self.save, 'w')
                json.dump({'date': self.today, 'arrays': self.arrays, 'results': self.results}, file, indent=4, ensure_ascii= False)
                file.close()
        self.daily = {}
        for k in self.results.keys():
            if self.results[k].get('watt_hours_day') is not None:
                whd = self.results[k]['watt_hours_day']
                for d in whd.keys():
                    if d not in self.daily.keys():
                        self.daily[d] = {'hourly': {}, 'kwh': 0.0}
                    self.daily[d]['kwh'] += whd[d] / 1000
            if self.results[k].get('watt_hours_period') is not None:
                whp = self.results[k]['watt_hours_period']
                for dt in whp.keys():
                    date = dt[:10]
                    hour = int(dt[11:13])
                    if hour not in self.daily[date]['hourly'].keys():
                        self.daily[date]['hourly'][hour] = 0.0
                    self.daily[date]['hourly'][hour] += whp[dt] / 1000
        # fill out hourly forecast to cover 24 hours
        for d in self.daily.keys():
            for h in range(0,24):
                if self.daily[d]['hourly'].get(h) is None:
                    self.daily[d]['hourly'][h] = 0.0
        # drop forecast for today as it already happened
        self.keys = sorted(self.daily.keys())
        self.days = len(self.keys)
        self.values = [self.daily[d]['kwh'] for d in self.keys]
        self.total = sum(self.values)
        if self.days > 0:
            self.avg = self.total / self.days
        return

    def __str__(self) :
        # return printable Solar info
        global debug_setting
        if not hasattr(self, 'days'):
            return 'Solar: no days in forecast'
        s = f'\nSolar yield for {self.days} days'
        for d in self.keys :
            y = self.daily[d]['kwh']
            day = datetime.strptime(d, '%Y-%m-%d').strftime('%A')[:3]
            s += f"\n   {d} {day} : {y:5.2f}kWh"
        return s

    def plot_daily(self) :
        if not hasattr(self, 'daily') :
            print(f"Solcast: no daily data to plot")
            return
        plt.figure(figsize=(figure_width, figure_width/3))
        print()
        # plot estimated
        x = [f"{d} {datetime.strptime(d, '%Y-%m-%d').strftime('%A')[:3]} " for d in self.keys if d <= self.today]
        y = [self.daily[d]['kwh'] for d in self.keys if d <= self.today]
        if x is not None and len(x) != 0 :
            plt.bar(x, y, color='green', linestyle='solid', label='estimated', linewidth=2)
        # plot forecasts
        x = [f"{d} {datetime.strptime(d, '%Y-%m-%d').strftime('%A')[:3]} " for d in self.keys if d > self.today]
        y = [self.daily[d]['kwh'] for d in self.keys if d > self.today]
        if x is not None and len(x) != 0 :
            plt.bar(x, y, color='orange', linestyle='solid', label='forecast', linewidth=2)
        # annotations
        if hasattr(self, 'avg') :
            plt.axhline(self.avg, color='blue', linestyle='solid', label=f'average {self.avg:.1f} kwh / day', linewidth=2)
        title = f"Solar daily yield on {self.today} for {self.days} days"
        title += f". Total yield = {self.total:.0f} kwh, Average = {self.avg:.0f}"    
        plt.title(title, fontsize=12)
        plt.grid()
#        plt.legend(fontsize=14, loc=legend_location)
        plt.xticks(rotation=45, ha='right')
        plt.show()
        return

    def plot_hourly(self, day = None) :
        if not hasattr(self, 'daily') :
            print(f"Solar: no daily data to plot")
            return
        if day == 'today':
            day = self.today
        elif day == 'tomorrow':
            day = self.tomorrow
        elif day == 'all':
            day = self.keys
        elif day is None:
            day = [self.today, self.tomorrow]
        if type(day) is list:
            for d in day:
                self.plot_hourly(d)
            return
        plt.figure(figsize=(figure_width, figure_width/3))
        print()
        if day is None:
            day = self.tomorrow
        elif day == 'today':
            day = self.today
        elif day == 'tomorrow':
            day = self.tomorrow
        # plot forecasts
        if self.daily.get(day) is None:
            print(f"Solar: no data for {day}")
            return
        hours = sorted([h for h in self.daily[day]['hourly'].keys()])
        x = [hours_time(h) for h in hours]
        y = [self.daily[day]['hourly'][h] for h in hours]
        color = 'orange' if day > self.today else 'green'
        if x is not None and len(x) != 0 :
            plt.plot(x, y, color=color, linestyle='solid', linewidth=2)
        title = f"Solar hourly yield on {day}"
        title += f". Total yield = {self.daily[day]['kwh']:.1f}kwh"    
        plt.title(title, fontsize=12)
        plt.grid()
        plt.xticks(rotation=45, ha='right')
        plt.show()
        return
