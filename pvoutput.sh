#! /usr/bin/bash
cd ${1:-.}
python << END

import foxess_cloud as f
import private as my
f.username = my.username
f.password = my.password
f.device_sn = my.device_sn
f.api_key = my.api_key
f.system_id = my.system_id
f.get_device(sn=my.device_sn)
f.set_pvoutput(tou=1, today=${2:-False})

END