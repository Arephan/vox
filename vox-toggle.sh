#!/bin/bash
if [ -f /tmp/vox-recording ]; then
    rm /tmp/vox-recording
else
    touch /tmp/vox-recording
fi
