#!/bin/bash
#export JAVA_HOME=$(/usr/libexec/java_home)
export JAVA_HOME=$( dirname $( dirname $( readlink -e /usr/bin/java ) ) )
atlas-run-standalone --product jira --version 6.4.1
