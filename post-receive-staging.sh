#!/bin/sh

STAGING_URL="/var/garage/rephoto_staging"
( cd $STAGING_URL && unset GIT_DIR && sudo git pull && sudo /opt/python2713/bin/python2.7 project/manage.py refresh --settings=ajapaik.settings && sudo /opt/python2713/bin/python2.7 project/manage.py refresh --settings=sift.settings )

PID=$(sudo supervisorctl status staging_ajapaik_ee | grep pid | sed -e "s/^.*pid \([0-9]*\).*$/\1/")
if [ "$PID" ]; then
        echo "Sending HUP to $PID"
        sudo kill -HUP $PID
else
        echo "uWSGI PID $PID not found!"
fi

echo "== STAGING UPDATED =="