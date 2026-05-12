"""
Created on Aug 28, 2018

@author: mjasnik
"""

# imports
from datetime import datetime
import re
import os
import shutil
import getpass

# timekpr imports
from timekpr.common.log import log
from timekpr.common.constants import constants as cons
from timekpr.common.utils.misc import findHourStartEndMinutes as findHourStartEndMinutes
from timekpr.common.utils.misc import splitConfigValueNameParam as splitConfigValueNameParam

# ## GLOBAL ##
# key pattern search
RE_SECTIONFINDER = re.compile(r"^\[([a-zA-Z0-9_\.@-]+)\]$")
RE_KEYVALUEFINDER = re.compile(r"^ *([A-Z]+[A-Z_]+[0-9]*) *= *(.*)$")
RE_ARRAYKEYFINDER = re.compile(r"^##([A-Z]+[A-Z_]+)##.*$")


def _saveConfigFile(pConfigFile, pKeyValuePairs):
    """Save the config file using custom helper function"""
    global RE_KEYVALUEFINDER, RE_ARRAYKEYFINDER
    # edit control file (using custom file rewrite method)
    # make a backup of the file
    shutil.copy(pConfigFile, pConfigFile + cons.TK_BAK_EXT)
    # read backup and write actual config file
    with open(pConfigFile + cons.TK_BAK_EXT, "r") as srcFile, open(pConfigFile, "w") as dstFile:
        # destination file
        dstLines = []
        # read line and do manipulations
        for rLine in srcFile:
            # def line
            line = rLine
            # if line matches parameter pattern, we look up for that key in our value list
            if RE_KEYVALUEFINDER.match(rLine):
                # check whether we can find the value for it
                key = RE_KEYVALUEFINDER.sub(r"\1", rLine.rstrip())
                # if key exists
                if key in pKeyValuePairs:
                    # in case of placeholder (value = None), just keep the line, else replace it
                    if pKeyValuePairs[key] is not None:
                        # now get the value
                        dstLines.append("%s = %s\n" % (key, pKeyValuePairs[key]))
                        # do not add original line
                        line = None
                else:
                    # do not add unknown options
                    line = None
            # search for variable options
            elif RE_ARRAYKEYFINDER.match(rLine):
                # check whether we can find the value for it
                key = RE_ARRAYKEYFINDER.sub(r"\1", rLine.rstrip())
                # now get the value
                dstLines.append("%s" % (rLine))
                # if key exists
                if key in pKeyValuePairs:
                    # append array of values
                    for rVal in pKeyValuePairs[key]:
                        # now get the value
                        dstLines.append("%s\n" % (rVal))
                # do not add original line
                line = None

            # append if there is a line
            if line is not None:
                # add line
                dstLines.append(line)

        # save config lines back to file
        dstFile.writelines(dstLines)


def _loadAndPrepareConfigFile(pTimekprConfig, pConfigFile, pLoadOnly=False):
    """Try to load config file, if that fails, try to read backup file"""
    global RE_KEYVALUEFINDER, RE_SECTIONFINDER
    # by default fail
    result = False
    # process primary and backup files
    for rFile in (pConfigFile, pConfigFile + cons.TK_BAK_EXT):
        # if file is ok
        if os.path.isfile(rFile) and os.path.getsize(rFile) != 0:
            # copy file back to original (if this is backup file)
            if rFile != pConfigFile and not pLoadOnly:
                # copy 
                shutil.copy(rFile, pConfigFile)
            # current section
            currSect = ""
            # read config file
            with open(pConfigFile, "r") as srcFile:
                # read line
                for rLine in srcFile:
                    # if line matches parameter pattern, we look up for that key in our value list
                    if RE_SECTIONFINDER.match(rLine):
                        # set up current section
                        currSect = RE_SECTIONFINDER.sub(r"\1", rLine.rstrip())
                        # found
                        if currSect != "":
                            # section found
                            pTimekprConfig["CONFIGLOADER"][currSect] = {}
                    elif currSect != "" and RE_KEYVALUEFINDER.match(rLine):
                        # value found
                        pTimekprConfig["CONFIGLOADER"][currSect][RE_KEYVALUEFINDER.sub(r"\1", rLine.rstrip())] = RE_KEYVALUEFINDER.sub(r"\2", rLine.rstrip())
                        # some values were read
                        result = True
            # we found smth
            if result:
                # stop
                break
        else:
            # we do not need empty files
            if os.path.isfile(rFile) and not pLoadOnly:
                # remove empty file
                os.remove(rFile)

    # result
    return result


def _readAndNormalizeValue(pTimekprConfig, pSection, pParam, pDefaultValue, pCheckValue, pOverallSuccess):
    """Read value from parser, if fails, then return default value"""
    # default values
    result = pOverallSuccess
    value = pDefaultValue
    try:
        # read value from config
        value = pTimekprConfig["CONFIGLOADER"][pSection][pParam]
        # check min / max if we have numbers
        if type(pDefaultValue).__name__ in ("int", "float"):
            value = int(value)
            # check
            if pCheckValue is not None:
                value = min(max(value, -pCheckValue), pCheckValue)
        # validate date format properly
        elif type(pDefaultValue).__name__ in ("date", "datetime"):
            value = datetime.strptime(value, cons.TK_DATETIME_FORMAT)
        # validate boolean properly
        elif type(pDefaultValue).__name__ in ("bool"):
            value = value.capitalize() == "True"
    except Exception:
        # default value
        value = pDefaultValue
        # failed
        result = False

    # return
    return result, value


def _cleanupValue(pValue):
    """Clean up value (basically remove stuff from begining and end)"""
    return(pValue.strip().strip(";") if pValue is not None else None)


class timekprConfig(object):
    """Main configuration class for the server"""

    def __init__(self):
        """Initialize stuff"""
        log.log(cons.TK_LOG_LEVEL_INFO, "initializing configuration manager")

        # config
        self._timekprConfig = {}

        # config sections from file
        self._timekprConfig["CONFIGLOADER"] = {}

        # in dev
        self._configDirPrefix = os.getcwd() if cons.TK_DEV_ACTIVE else ""
        # main config
        self._timekprConfig["TIMEKPR_MAIN_CONFIG_DIR"] = os.path.join(self._configDirPrefix, (cons.TK_MAIN_CONFIG_DIR_DEV if cons.TK_DEV_ACTIVE else cons.TK_MAIN_CONFIG_DIR))
        self._configFile = os.path.join(self._timekprConfig["TIMEKPR_MAIN_CONFIG_DIR"], cons.TK_MAIN_CONFIG_FILE)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish configuration manager")

    def __del__(self):
        """De-initialize stuff"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-initializing configuration manager")

    def loadMainConfiguration(self):
        """Read main timekpr config file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading configuration")

        # try to load config file
        result = _loadAndPrepareConfigFile(self._timekprConfig, self._configFile)
        # value read result
        resultValue = True

        # read config failed, we need to initialize
        if not result:
            # logging
            log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: could not parse main configuration file (%s) properly, will use default values" % (self._configFile))
            # init config
            self.initDefaultConfiguration()
            # re-read the file
            result = _loadAndPrepareConfigFile(self._timekprConfig, self._configFile)

        # general section
        section = "GENERAL"
        # read
        param = "TIMEKPR_VERSION"
        self._timekprConfig[param] = cons.TK_VERSION
        # read
        param = "TIMEKPR_LOGLEVEL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_USR_LOG_LEVEL_INFO, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_POLLTIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_POLLTIME, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_SAVE_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_SAVE_INTERVAL, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_TRACK_INACTIVE"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_TRACK_INACTIVE, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_TERMINATION_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_TERMINATION_TIME, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_FINAL_WARNING_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_FINAL_COUNTDOWN_TIME, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_FINAL_NOTIFICATION_TIME, pCheckValue=None, pOverallSuccess=resultValue)

        # session section
        section = "SESSION"
        # read
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_SESSION_TYPES_CTRL, pCheckValue=None, pOverallSuccess=resultValue)
        self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])
        # read
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_SESSION_TYPES_EXCL, pCheckValue=None, pOverallSuccess=resultValue)
        self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])
        # read
        param = "TIMEKPR_USERS_EXCL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_USERS_EXCL, pCheckValue=None, pOverallSuccess=resultValue)
        self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])

        # directory section (! in case directories are not correct, they are not overwritten with defaults !)
        section = "DIRECTORIES"
        # read
        param = "TIMEKPR_CONFIG_DIR"
        result, value = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_CONFIG_DIR, pCheckValue=None, pOverallSuccess=result)
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)
        # read
        param = "TIMEKPR_WORK_DIR"
        result, value = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_WORK_DIR, pCheckValue=None, pOverallSuccess=result)
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)
        # read
        param = "TIMEKPR_SHARED_DIR"
        result, value = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_SHARED_DIR, pCheckValue=None, pOverallSuccess=result)
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)
        # read
        param = "TIMEKPR_LOGFILE_DIR"
        result, value = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_LOGFILE_DIR, pCheckValue=None, pOverallSuccess=result)
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)

        # global PlayTime config section
        section = "PLAYTIME"
        # read
        param = "TIMEKPR_PLAYTIME_ENABLED"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_PLAYTIME_ENABLED, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=cons.TK_PLAYTIME_ENABLED, pCheckValue=None, pOverallSuccess=resultValue)

        # global misc config section
        section = "MISC"
        # read
        param = "TIMEKPR_USE_LOCKED_HINT"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(self._timekprConfig, section, param, pDefaultValue=True, pCheckValue=None, pOverallSuccess=resultValue)

        # if we could not read some values, save what we could + defaults
        if not resultValue:
            # logging
            log.log(cons.TK_LOG_LEVEL_WARN, "WARNING: some values in main config file (%s) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead" % (self._configFile))
            # save what we could
            self.initDefaultConfiguration(True)

        # if we could not read some values, report that (directories only)
        if not result:
            # logging
            log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: some directory values in main config file (%s) could not be read, valid values and defaults used (config NOT overwritten)" % (self._configFile))

        # clear
        self._timekprConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading configuration")

        # result
        return True

    def initDefaultConfiguration(self, pReuseValues=False):
        """Save config file (if someone messed up config file, we have to write new one)"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start saving default configuration")

        # build the file
        dstLines = []

        # save default config
        section = "DOCUMENTATION"
        # section
        dstLines += ["[%s]\n" % section]
        # values / comments
        dstLines += ["%s\n" % "#### this is the main configuration file for timekpr-next"]
        dstLines += ["%s\n" % "#### if this file cannot be read properly, it will be overwritten with defaults"]

        # section
        section = "GENERAL"
        # values / comments
        dstLines += ["\n[%s]\n" % section]
        dstLines += ["%s\n" % "#### general configuration section"]
        # set up param
        param = "TIMEKPR_LOGLEVEL"
        dstLines += ["%s\n" % "# this defines logging level of the timekpr (1 - normal, 2 - debug, 3 - extra debug)"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_USR_LOG_LEVEL_INFO))]
        # set up param
        param = "TIMEKPR_POLLTIME"
        dstLines += ["%s\n" % "# this defines polling time (in memory) in seconds"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_POLLTIME))]
        # set up param
        param = "TIMEKPR_SAVE_TIME"
        dstLines += ["%s\n" % "# this defines a time for saving user time control file (polling and accounting is done in memory more often, but saving is not)"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_SAVE_INTERVAL))]
        # set up param
        param = "TIMEKPR_TRACK_INACTIVE"
        dstLines += ["%s\n" % "# this defines whether to account sessions which are inactive (locked screen, user switched away from desktop, etc.),"]
        dstLines += ["%s\n" % "#   new users, when created, will inherit this value"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_TRACK_INACTIVE))]
        # set up param
        param = "TIMEKPR_TERMINATION_TIME"
        dstLines += ["%s\n" % "# this defines a time interval in seconds prior to assign user a termination sequence"]
        dstLines += ["%s\n" % "#   15 seconds before time ends nothing can be done to avoid killing a session"]
        dstLines += ["%s\n" % "#   this also is the time before initiating a termination sequence if user has logged in inappropriate time"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_TERMINATION_TIME))]
        # set up param
        param = "TIMEKPR_FINAL_WARNING_TIME"
        dstLines += ["%s\n" % "# this defines a time interval prior to termination of user sessions when timekpr will send continous final warnings (countdown) until the actual termination"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_FINAL_COUNTDOWN_TIME))]
        # set up param
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        dstLines += ["%s\n" % "# this defines a time interval prior to termination of user sessions when timekpr will send one final warning about time left"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_FINAL_NOTIFICATION_TIME))]

        # section
        section = "SESSION"
        # values / comments
        dstLines += ["\n[%s]\n" % section]
        dstLines += ["%s\n" % "#### this section contains configuration about sessions"]
        # set up param
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        dstLines += ["%s\n" % "# session types timekpr will track"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_SESSION_TYPES_CTRL)]
        # set up param
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        dstLines += ["%s\n" % "# session types timekpr will ignore explicitly"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_SESSION_TYPES_EXCL)]
        # set up param
        param = "TIMEKPR_USERS_EXCL"
        dstLines += ["%s\n" % "# users timekpr will ignore explicitly"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_USERS_EXCL)]

        # section
        section = "DIRECTORIES"
        # values / comments
        dstLines += ["\n[%s]\n" % section]
        dstLines += ["%s\n" % "#### this section contains directory configuration"]
        # set up param
        param = "TIMEKPR_CONFIG_DIR"
        dstLines += ["%s\n" % "# runtime directory for timekpr user configuration files"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_CONFIG_DIR)]
        # set up param
        param = "TIMEKPR_WORK_DIR"
        dstLines += ["%s\n" % "# runtime directory for timekpr time control files"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_WORK_DIR)]
        # set up param
        param = "TIMEKPR_SHARED_DIR"
        dstLines += ["%s\n" % "# directory for shared files (images, gui definitions, etc.)"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_SHARED_DIR)]
        # set up param
        param = "TIMEKPR_LOGFILE_DIR"
        dstLines += ["%s\n" % "# directory for log files"]
        dstLines += [ "%s = %s\n" % (param, self._timekprConfig[param] if pReuseValues else cons.TK_LOGFILE_DIR)]

        # section
        section = "MISC"
        # values / comments
        dstLines += ["\n[%s]\n" % section]
        dstLines += ["%s\n" % "#### this section contains global misc configuration"]
        # set up param
        param = "TIMEKPR_USE_LOCKED_HINT"
        dstLines += ["%s\n" % "# whether timekpr should use LockedHint for screen locking detection from login1 (it is/was quite buggy)"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(True))]

        # section
        section = "PLAYTIME"
        # values / comments
        dstLines += ["\n[%s]\n" % section]
        dstLines += ["%s\n" % "#### this section contains global PlayTime activity configuration"]
        # set up param
        param = "TIMEKPR_PLAYTIME_ENABLED"
        dstLines += ["%s\n" % "# whether PlayTime is enabled globally"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_PLAYTIME_ENABLED))]
        # set up param
        param = "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED"
        dstLines += ["%s\n" % "# whether PlayTime activity monitor will use process command line, including arguments, for monitoring processes (by default only uses the process name)"]
        dstLines += [ "%s = %s\n" % (param, str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_PLAYTIME_ENABLED))]

        # write config file
        with open(self._configFile, "w") as dstFile:
            # save the file
            dstFile.writelines(dstLines)

        # clear
        self._timekprConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_INFO, "finish saving default configuration")

    def saveTimekprConfiguration(self):
        """Write new sections of the file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start saving timekpr configuration")

        # init dict
        values = {}

        # server loglevel
        param = "TIMEKPR_LOGLEVEL"
        values[param] = str(self._timekprConfig[param])
        # in-memory polling time
        param = "TIMEKPR_POLLTIME"
        values[param] = str(self._timekprConfig[param])
        # time interval to save user spent time
        param = "TIMEKPR_SAVE_TIME"
        values[param] = str(self._timekprConfig[param])
        # track inactive (default value)
        param = "TIMEKPR_TRACK_INACTIVE"
        values[param] = str(self._timekprConfig[param])
        # termination time (allowed login time when there is no time left before user is thrown out)
        param = "TIMEKPR_TERMINATION_TIME"
        values[param] = str(self._timekprConfig[param])
        # final warning time (countdown to 0 before terminating session)
        param = "TIMEKPR_FINAL_WARNING_TIME"
        values[param] = str(self._timekprConfig[param])
        # final notification time (final warning before terminating session)
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        values[param] = str(self._timekprConfig[param])
        # which session types to control
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        values[param] = str(self._timekprConfig[param])
        # explicitly excludeds ession types (do not count time in these sessions)
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        values[param] = str(self._timekprConfig[param])
        # which users to exclude from time accounting
        param = "TIMEKPR_USERS_EXCL"
        values[param] = str(self._timekprConfig[param])

        # whether use of LockedHint is enabled
        param = "TIMEKPR_USE_LOCKED_HINT"
        values[param] = str(self._timekprConfig[param])

        # whether PlayTime is enabled
        param = "TIMEKPR_PLAYTIME_ENABLED"
        values[param] = str(self._timekprConfig[param])
        # whether PlayTime enhanced activity monitor is enabled
        param = "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED"
        values[param] = str(self._timekprConfig[param])

        # ## pass placeholders for directories ##
        # config dir
        param = "TIMEKPR_CONFIG_DIR"
        values[param] = None
        # work dir
        param = "TIMEKPR_WORK_DIR"
        values[param] = None
        # shared dir
        param = "TIMEKPR_SHARED_DIR"
        values[param] = None
        # log dir
        param = "TIMEKPR_LOGFILE_DIR"
        values[param] = None

        # edit client config file (using alternate method)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish saving timekpr configuration")

    def logMainConfiguration(self):
        """Log main timekpr config file"""
        # log
        log.log(cons.TK_LOG_LEVEL_INFO, "main configuration:")

        try:
            # log
            param = "TIMEKPR_LOGLEVEL"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_POLLTIME"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_SAVE_TIME"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_TRACK_INACTIVE"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_TERMINATION_TIME"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_FINAL_WARNING_TIME"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))

            # log
            param = "TIMEKPR_SESSION_TYPES_CTRL"
            self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_SESSION_TYPES_EXCL"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_USERS_EXCL"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))

            # log
            param = "TIMEKPR_USE_LOCKED_HINT"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))

            # log
            param = "TIMEKPR_PLAYTIME_ENABLED"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
            # log
            param = "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprConfig[param])))
        # fail
        except Exception:
            # log
            log.log(cons.TK_LOG_LEVEL_INFO, "  configuration log failed")

    def getTimekprVersion(self):
        """Get version"""
        # param
        param = "TIMEKPR_VERSION"
        # result
        return self._timekprConfig[param]

    def getTimekprLogLevel(self):
        """Get logging level"""
        # param
        param = "TIMEKPR_LOGLEVEL"
        # result
        return self._timekprConfig[param]

    def getTimekprPollTime(self):
        """Get polling time"""
        # param
        param = "TIMEKPR_POLLTIME"
        # result
        return self._timekprConfig[param]

    def getTimekprSaveTime(self):
        """Get save time"""
        # param
        param = "TIMEKPR_SAVE_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprTrackInactive(self):
        """Get tracking inactive"""
        # param
        param = "TIMEKPR_TRACK_INACTIVE"
        # result
        return self._timekprConfig[param]

    def getTimekprTerminationTime(self):
        """Get termination time"""
        # param
        param = "TIMEKPR_TERMINATION_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprFinalWarningTime(self):
        """Get final warning time"""
        # param
        param = "TIMEKPR_FINAL_WARNING_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprFinalNotificationTime(self):
        """Get final notification time"""
        # param
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprSessionsCtrl(self):
        """Get sessions to control"""
        # param
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        # result
        return [rVal.strip() for rVal in self._timekprConfig[param].split(";") if rVal != ""] if param in self._timekprConfig else []

    def getTimekprSessionsExcl(self):
        """Get sessions to exclude"""
        # param
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        # result
        return [rVal.strip() for rVal in self._timekprConfig[param].split(";") if rVal != ""] if param in self._timekprConfig else []

    def getTimekprUsersExcl(self):
        """Get sessions to exclude"""
        # param
        param = "TIMEKPR_USERS_EXCL"
        # result
        return [rVal.strip() for rVal in self._timekprConfig[param].split(";") if rVal != ""] if param in self._timekprConfig else []

    def getTimekprConfigDir(self):
        """Get config dir"""
        # param
        param = "TIMEKPR_CONFIG_DIR"
        # result
        return cons.TK_CONFIG_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]

    def getTimekprWorkDir(self):
        """Get working dir"""
        # param
        param = "TIMEKPR_WORK_DIR"
        # result
        return cons.TK_WORK_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]

    def getTimekprSharedDir(self):
        """Get shared dir"""
        # param
        param = "TIMEKPR_SHARED_DIR"
        # result
        return cons.TK_SHARED_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]

    def getTimekprLogfileDir(self):
        """Get log file dir"""
        # param
        param = "TIMEKPR_LOGFILE_DIR"
        # result
        return cons.TK_LOGFILE_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]

    def getTimekprUseLockedHintEnabled(self):
        """Return whether we can use buggy LockedHint from login1 to detect inactivity"""
        # param
        param = "TIMEKPR_USE_LOCKED_HINT"
        # result
        return self._timekprConfig[param]

    def getTimekprPlayTimeEnabled(self):
        """Return whether we have PlayTime enabled"""
        # param
        param = "TIMEKPR_PLAYTIME_ENABLED"
        # result
        return self._timekprConfig[param]

    def getTimekprPlayTimeEnhancedActivityMonitorEnabled(self):
        """Return whether we have PlayTime enhanced activity monitor is enabled"""
        # param
        param = "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED"
        # result
        return self._timekprConfig[param]

    def getTimekprLastModified(self):
        """Get last file modification time"""
        # result
        return datetime.fromtimestamp(os.path.getmtime(self._configFile))

    def setTimekprLogLevel(self, pLogLevel):
        """Set logging level"""
        # set
        self._timekprConfig["TIMEKPR_LOGLEVEL"] = pLogLevel

    def setTimekprPollTime(self, pPollingTimeSecs):
        """Set polling time"""
        # set
        self._timekprConfig["TIMEKPR_POLLTIME"] = pPollingTimeSecs

    def setTimekprSaveTime(self, pSaveTimeSecs):
        """Set save time"""
        # set
        self._timekprConfig["TIMEKPR_SAVE_TIME"] = pSaveTimeSecs

    def setTimekprTrackInactive(self, pTrackInactiveDefault):
        """Get tracking inactive"""
        # set
        self._timekprConfig["TIMEKPR_TRACK_INACTIVE"] = pTrackInactiveDefault

    def setTimekprTerminationTime(self, pTerminationTimeSecs):
        """Set termination time"""
        # set
        self._timekprConfig["TIMEKPR_TERMINATION_TIME"] = pTerminationTimeSecs

    def setTimekprFinalWarningTime(self, pFinalWarningTimeSecs):
        """Set final warning time"""
        # set
        self._timekprConfig["TIMEKPR_FINAL_WARNING_TIME"] = pFinalWarningTimeSecs

    def setTimekprFinalNotificationTime(self, pFinalNotificationTimeSecs):
        """Set final warning time"""
        # set
        self._timekprConfig["TIMEKPR_FINAL_NOTIFICATION_TIME"] = pFinalNotificationTimeSecs

    def setTimekprSessionsCtrl(self, pSessionsCtrl):
        """Set sessions to control"""
        # set
        self._timekprConfig["TIMEKPR_SESSION_TYPES_CTRL"] = ";".join(pSessionsCtrl)

    def setTimekprSessionsExcl(self, pSessionsExcl):
        """Set sessions to exclude"""
        # set
        self._timekprConfig["TIMEKPR_SESSION_TYPES_EXCL"] = ";".join(pSessionsExcl)

    def setTimekprUsersExcl(self, pUsersExcl):
        """Set sessions to exclude"""
        # set
        self._timekprConfig["TIMEKPR_USERS_EXCL"] = ";".join(pUsersExcl)

    def setTimekprUseLockedHintEnabled(self, pUseLockedHint):
        """Set use LockedHint enable flag"""
        # set
        self._timekprConfig["TIMEKPR_USE_LOCKED_HINT"] = pUseLockedHint

    def setTimekprPlayTimeEnabled(self, pPlayTimeEnabled):
        """Set PlayTime enable flag"""
        # set
        self._timekprConfig["TIMEKPR_PLAYTIME_ENABLED"] = pPlayTimeEnabled

    def setTimekprPlayTimeEnhancedActivityMonitorEnabled(self, pPlayTimeAdvancedSearchEnabled):
        """Set PlayTime enable flag"""
        # set
        self._timekprConfig["TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED"] = pPlayTimeAdvancedSearchEnabled


class timekprUserConfig(object):
    """Class will contain and provide config related functionality"""

    def __init__(self, pDirectory, pUserName):
        """Initialize config"""

        log.log(cons.TK_LOG_LEVEL_INFO, "init user (%s) configuration manager" % (pUserName))

        # initialize class variables
        self._configFile = os.path.join(pDirectory, cons.TK_USER_CONFIG_FILE % (pUserName))
        self._userName = pUserName
        self._timekprUserConfig = {}

        # config sections from file
        self._timekprUserConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_INFO, "finish user configuration manager")

    def __del__(self):
        """De-initialize config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-init user configuration manager")

    def loadUserConfiguration(self, pValidateOnly=False):
        """Read user timekpr config file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start load user configuration")

        # user config section
        section = self._userName
        # try to load config file
        result = _loadAndPrepareConfigFile(self._timekprUserConfig, self._configFile)
        # value read result
        resultValue = True
        # if we still are fine (and not just checking)
        if not pValidateOnly or (pValidateOnly and result):
            # read config failed, we need to initialize
            if not result:
                # logging
                log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: could not parse the main configuration file (%s) properly, will use default values" % (self._configFile))
                # init config
                self.initUserConfiguration()
                # re-read the file
                result = _loadAndPrepareConfigFile(self._timekprUserConfig, self._configFile)

            # read
            param = "ALLOWED_HOURS"
            for i in range(1, 7+1):
                resultValue, self._timekprUserConfig["%s_%s" % (param, str(i))] = _readAndNormalizeValue(self._timekprUserConfig, section, ("%s_%s" % (param, str(i))), pDefaultValue=cons.TK_ALLOWED_HOURS, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "ALLOWED_WEEKDAYS"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_ALLOWED_WEEKDAYS, pCheckValue=None, pOverallSuccess=resultValue)
            self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])
            # read
            param = "LIMITS_PER_WEEKDAYS"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_LIMITS_PER_WEEKDAYS, pCheckValue=None, pOverallSuccess=resultValue)
            self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])
            # read
            param = "LIMIT_PER_WEEK"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_LIMIT_PER_WEEK, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "LIMIT_PER_MONTH"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_LIMIT_PER_MONTH, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "TRACK_INACTIVE"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_TRACK_INACTIVE, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "HIDE_TRAY_ICON"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_HIDE_TRAY_ICON, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "LOCKOUT_TYPE"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_CTRL_RES_T, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "WAKEUP_HOUR_INTERVAL"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue="0;23", pCheckValue=None, pOverallSuccess=resultValue)
            self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])

            # user PlayTime config section
            section = "%s.%s" % (self._userName, "PLAYTIME")
            # read
            param = "PLAYTIME_ENABLED"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_PLAYTIME_ENABLED, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "PLAYTIME_LIMIT_OVERRIDE_ENABLED"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_PLAYTIME_ENABLED, pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=(not cons.TK_PLAYTIME_ENABLED), pCheckValue=None, pOverallSuccess=resultValue)
            # read
            param = "PLAYTIME_ALLOWED_WEEKDAYS"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_PLAYTIME_ALLOWED_WEEKDAYS, pCheckValue=None, pOverallSuccess=resultValue)
            self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])
            # read
            param = "PLAYTIME_LIMITS_PER_WEEKDAYS"
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(self._timekprUserConfig, section, param, pDefaultValue=cons.TK_PLAYTIME_LIMITS_PER_WEEKDAYS, pCheckValue=None, pOverallSuccess=resultValue)
            self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])
            # read activities
            self._timekprUserConfig["PLAYTIME_ACTIVITIES"] = []
            appCfgKeys = [rParam for rParam in self._timekprUserConfig["CONFIGLOADER"][section] if "PLAYTIME_ACTIVITY_" in rParam] if section in self._timekprUserConfig["CONFIGLOADER"] else []
            # read all apps (apps have to be properly configured)
            for rAppIdx in range(0, len(appCfgKeys)):
                # read value
                resultValue, process = _readAndNormalizeValue(self._timekprUserConfig, section, appCfgKeys[rAppIdx], pDefaultValue=None, pCheckValue=None, pOverallSuccess=resultValue)
                # read successful
                if process is not None:
                    # add to the activities list
                    proc, desc = splitConfigValueNameParam(process)
                    # we have valid process
                    if proc is not None:
                        # save process
                        self._timekprUserConfig["PLAYTIME_ACTIVITIES"].append([proc, desc])
            # log
            log.log(cons.TK_LOG_LEVEL_DEBUG, "PT: found total %i activities, valid %i" % (len(appCfgKeys), len(self._timekprUserConfig["PLAYTIME_ACTIVITIES"])))

            # if we could not read some values, save what we could + defaults
            if not resultValue:
                # logging
                log.log(cons.TK_LOG_LEVEL_WARN, "WARNING: some values in user config file (%s) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead" % (self._configFile))
                # init config with partial values read and save what we could
                self.initUserConfiguration(True)

        # clear
        self._timekprUserConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish load user configuration")

        # result
        return result

    def initUserConfiguration(self, pReuseValues=False):
        """Write new sections of the file"""
        log.log(cons.TK_LOG_LEVEL_INFO, "init default user (%s) configuration" % (self._userName))

        # build the file
        dstLines = []

        # save default config
        section = "DOCUMENTATION"
        # section
        dstLines += ["[%s]\n" % section]
        # values / comments
        dstLines += ["%s\n" % "#### this is the user configuration file for timekpr-next"]
        dstLines += ["%s\n" % "#### if this file cannot be read properly, it will be overwritten with defaults"]
        dstLines += ["%s\n" % "#### all numeric time values are specified in seconds"]
        dstLines += ["%s\n" % "#### days and hours should be configured as per ISO 8601 (i.e. Monday is the first day of week (1-7) and hours are in 24h format (0-23))"]

        # add new user section
        section = self._userName
        # section
        dstLines += ["\n[%s]\n" % section]
        # values / comments
        dstLines += ["%s\n" % "# this defines which hours are allowed (remove or add hours to limit access), configure limits for start/end minutes for hour in brackets,"]
        dstLines += ["%s\n" % "#   optionally enter ! in front of hour to mark it non-accountable, example: !22[00-15]"]
        # set up param
        param = "ALLOWED_HOURS"
        # set hours for all days
        for i in range(1, 7+1):
            dstLines += ["%s_%s = %s\n" % (param, str(i), self._timekprUserConfig["%s_%s" % (param, str(i))] if pReuseValues else cons.TK_ALLOWED_HOURS)]
        # set up param
        param = "ALLOWED_WEEKDAYS"
        dstLines += ["%s\n" % "# this defines which days of the week a user can use computer (remove or add days to limit access)"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserConfig[param] if pReuseValues else cons.TK_ALLOWED_WEEKDAYS)]
        # set up param
        param = "LIMITS_PER_WEEKDAYS"
        dstLines += ["%s\n" % "# this defines allowed time in seconds per week day a user can use the computer (number of values must match the number of values for option ALLOWED_WEEKDAYS)"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserConfig[param] if pReuseValues else cons.TK_LIMITS_PER_WEEKDAYS)]
        # set up param
        param = "LIMIT_PER_WEEK"
        dstLines += ["%s\n" % "# this defines allowed time per week in seconds (in addition to other limits)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(cons.TK_LIMIT_PER_WEEK))]
        # set up param
        param = "LIMIT_PER_MONTH"
        dstLines += ["%s\n" % "# this defines allowed time per month in seconds (in addition to other limits)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(cons.TK_LIMIT_PER_MONTH))]
        # set up param
        param = "TRACK_INACTIVE"
        dstLines += ["%s\n" % "# this defines whether to account sessions which are inactive (locked screen, user switched away from desktop, etc.)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(cons.TK_TRACK_INACTIVE))]
        # set up param
        param = "HIDE_TRAY_ICON"
        dstLines += ["%s\n" % "# this defines whether to show icon and notifications for user"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(cons.TK_HIDE_TRAY_ICON))]
        # set up param
        param = "LOCKOUT_TYPE"
        dstLines += ["%s\n" % "# this defines user restriction / lockout mode: lock - lock screen, suspend - put computer to sleep, suspendwake - put computer to sleep and wake it up,"]
        dstLines += ["%s\n" % "#   terminate - terminate sessions, kill - kill sessions, shutdown - shutdown the computer"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserConfig[param] if pReuseValues else cons.TK_CTRL_RES_T)]
        # set up param
        param = "WAKEUP_HOUR_INTERVAL"
        dstLines += ["%s\n" % "# this defines wakeup hour interval in format xn;yn where xn / yn are hours from 0 to 23, wakeup itself must be supported by BIOS / UEFI and enabled,"]
        dstLines += ["%s\n" % "#   this is effective only when lockout type is suspendwake"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserConfig[param] if pReuseValues else "0;23")]

        # PlayTime
        section = "%s.%s" % (self._userName, "PLAYTIME")
        # section
        dstLines += ["\n[%s]\n" % section]
        # set up param
        param = "PLAYTIME_ENABLED"
        dstLines += ["%s\n" % "# whether PlayTime is enabled for this user"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(cons.TK_PLAYTIME_ENABLED))]
        # set up param
        param = "PLAYTIME_LIMIT_OVERRIDE_ENABLED"
        dstLines += ["%s\n" % "# whether PlayTime is enabled to override existing time accounting, i.e. time ticks only when PlayTime processes / activities are running,"]
        dstLines += ["%s\n" % "#   in this case explicit PlayTime limits are ignored"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(cons.TK_PLAYTIME_ENABLED))]
        # set up param
        param = "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"
        dstLines += ["%s\n" % "# whether PlayTime activities are allowed during unaccounted time intervals"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserConfig[param]) if pReuseValues else str(not cons.TK_PLAYTIME_ENABLED))]
        # set up param
        param = "PLAYTIME_ALLOWED_WEEKDAYS"
        dstLines += ["%s\n" % "# specify on which days PlayTime is enabled"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserConfig[param] if pReuseValues else cons.TK_PLAYTIME_ALLOWED_WEEKDAYS)]
        # set up param
        param = "PLAYTIME_LIMITS_PER_WEEKDAYS"
        dstLines += ["%s\n" % "# how much PlayTime is allowed per allowed days (number of values must match the number of values for option PLAYTIME_ALLOWED_WEEKDAYS)"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserConfig[param] if pReuseValues else cons.TK_PLAYTIME_LIMITS_PER_WEEKDAYS)]
        # set up param
        dstLines += ["%s\n" % "# this defines which activities / processes are monitored, pattern: PLAYTIME_ACTIVITY_NNN = PROCESS_MASK[DESCRIPTION],"]
        dstLines += ["%s\n" % "#   where NNN is number left padded with 0 (keys must be unique and ordered), optionally it's possible to add user"]
        dstLines += ["%s\n" % "#   friendly description in [] brackets. Process mask supports regexp, except symbols [], please be careful entering it!"]
        dstLines += ["%s\n" % "##PLAYTIME_ACTIVITIES## Do NOT remove or alter this line!"]
        # save all activity values (activities are varying list), do this only if values are reused
        for rPTAppIdx in range(0, len(self._timekprUserConfig["PLAYTIME_ACTIVITIES"]) if pReuseValues else 0):
            # write all to file
            param = "PLAYTIME_ACTIVITY_%s" % (str(rPTAppIdx+1).rjust(3, "0"))
            act = self._timekprUserConfig["PLAYTIME_ACTIVITIES"][rPTAppIdx][0]
            desc = self._timekprUserConfig["PLAYTIME_ACTIVITIES"][rPTAppIdx][1]
            dstLines += ["%s = %s\n" % (param, "%s[%s]" % (act, desc) if desc is not None else "%s" % (act))]

        # write config file
        with open(self._configFile, "w") as dstFile:
            # save the file
            dstFile.writelines(dstLines)

        # clear
        self._timekprUserConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init default user configuration")

    def saveUserConfiguration(self):
        """Write new sections of the file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start saving new user (%s) configuration" % (self._userName))

        # init dict
        values = {}

        # allowed weekdays
        param = "ALLOWED_WEEKDAYS"
        values[param] = self._timekprUserConfig[param]
        # allowed hours for every week day
        for rDay in range(1, 7+1):
            param = "ALLOWED_HOURS_%s" % (str(rDay))
            values[param] = self._timekprUserConfig[param]
        # limits per weekdays
        param = "LIMITS_PER_WEEKDAYS"
        values[param] = self._timekprUserConfig[param]
        # limits per week
        param = "LIMIT_PER_WEEK"
        values[param] = str(self._timekprUserConfig[param])
        # limits per month
        param = "LIMIT_PER_MONTH"
        values[param] = str(self._timekprUserConfig[param])
        # track inactive
        param = "TRACK_INACTIVE"
        values[param] = str(self._timekprUserConfig[param])
        # try icon
        param = "HIDE_TRAY_ICON"
        values[param] = str(self._timekprUserConfig[param])
        # restriction / lockout type
        param = "LOCKOUT_TYPE"
        values[param] = self._timekprUserConfig[param]
        # wakeup hour interval
        param = "WAKEUP_HOUR_INTERVAL"
        values[param] = self._timekprUserConfig[param]

        # PlayTime config
        # PlayTime enabled
        param = "PLAYTIME_ENABLED"
        values[param] = str(self._timekprUserConfig[param])
        # PlayTime override enabled
        param = "PLAYTIME_LIMIT_OVERRIDE_ENABLED"
        values[param] = str(self._timekprUserConfig[param])
        # PlayTime allowed during unaccounted intervals
        param = "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"
        values[param] = str(self._timekprUserConfig[param])
        # PlayTime allowed weekdays
        param = "PLAYTIME_ALLOWED_WEEKDAYS"
        values[param] = self._timekprUserConfig[param]
        # PlayTime limits per weekdays
        param = "PLAYTIME_LIMITS_PER_WEEKDAYS"
        values[param] = str(self._timekprUserConfig[param])
        # PlayTime activities
        param = "PLAYTIME_ACTIVITIES"
        values[param] = []
        # save all activity values
        for rPTAppIdx in range(0, len(self._timekprUserConfig[param])):
            # write all to file
            subparam = "PLAYTIME_ACTIVITY_%s" % (str(rPTAppIdx + 1).rjust(3, "0"))
            act = self._timekprUserConfig["PLAYTIME_ACTIVITIES"][rPTAppIdx][0]
            desc = self._timekprUserConfig["PLAYTIME_ACTIVITIES"][rPTAppIdx][1]
            values[param].append("%s = %s[%s]" % (subparam, act, desc) if desc is not None else "%s = %s" % (subparam, act))

        # edit client config file (using alternate method)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish saving new user configuration")

    def logUserConfiguration(self):
        """Log user timekpr config file"""
        # log
        log.log(cons.TK_LOG_LEVEL_INFO, "user \"%s\" configuration:" % (self._userName))

        try:
            # log
            param = "ALLOWED_HOURS"
            for i in range(1, 7+1):
                paramN = "%s_%i" % (param, i)
                log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (paramN, str(self._timekprUserConfig[paramN])))
            # log
            param = "ALLOWED_WEEKDAYS"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "LIMITS_PER_WEEKDAYS"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "LIMIT_PER_WEEK"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "LIMIT_PER_MONTH"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "TRACK_INACTIVE"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "HIDE_TRAY_ICON"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "LOCKOUT_TYPE"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "WAKEUP_HOUR_INTERVAL"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))

            # log
            param = "PLAYTIME_ENABLED"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "PLAYTIME_LIMIT_OVERRIDE_ENABLED"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "PLAYTIME_ALLOWED_WEEKDAYS"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log
            param = "PLAYTIME_LIMITS_PER_WEEKDAYS"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserConfig[param])))
            # log activities
            log.log(cons.TK_LOG_LEVEL_INFO, "  PT activities:")
            for rV in self._timekprUserConfig["PLAYTIME_ACTIVITIES"]:
                log.log(cons.TK_LOG_LEVEL_INFO, "    %s=%s" % (rV[0], rV[1]))
        # fail
        except Exception:
            # log
            log.log(cons.TK_LOG_LEVEL_INFO, "  configuration log failed")

    def getUserAllowedHours(self, pDay):
        """Get allowed hours"""
        # this is the dict for hour config
        allowedHours = {}

        # get allowed hours for all of the week days
        param = "ALLOWED_HOURS_%s" % (pDay)
        # minutes can be specified in brackets after hour
        if self._timekprUserConfig[param] != "":
            for rHour in self._timekprUserConfig[param].split(";"):
                # determine hour and minutes
                hour, sMin, eMin, uacc = findHourStartEndMinutes(rHour)
                # hour is correct
                if hour is not None:
                    # get our dict done
                    allowedHours[str(hour)] = {cons.TK_CTRL_SMIN: sMin, cons.TK_CTRL_EMIN: eMin, cons.TK_CTRL_UACC: uacc}
        # result
        return allowedHours

    def getUserAllowedWeekdays(self):
        """Get allowed week days"""
        # param
        param = "ALLOWED_WEEKDAYS"
        # result
        return [rVal.strip() for rVal in self._timekprUserConfig[param].split(";") if rVal != ""]

    def getUserLimitsPerWeekdays(self):
        """Get allowed limits per week day"""
        # param
        param = "LIMITS_PER_WEEKDAYS"
        # result
        return [int(rVal.strip()) for rVal in self._timekprUserConfig[param].split(";") if rVal != ""]

    def getUserWeekLimit(self):
        """Get limit per week"""
        # result
        return self._timekprUserConfig["LIMIT_PER_WEEK"]

    def getUserMonthLimit(self):
        """Get limit per month"""
        # result
        return self._timekprUserConfig["LIMIT_PER_MONTH"]

    def getUserTrackInactive(self):
        """Get whether to track inactive sessions"""
        # result
        return self._timekprUserConfig["TRACK_INACTIVE"]

    def getUserHideTrayIcon(self):
        """Get whether to hide icon and notifications"""
        # result
        return self._timekprUserConfig["HIDE_TRAY_ICON"]

    def getUserLockoutType(self):
        """Get user restriction / lockout type"""
        # result
        return self._timekprUserConfig["LOCKOUT_TYPE"]

    def getUserWakeupHourInterval(self):
        """Get user wakeup hour intervals"""
        # param
        param = "WAKEUP_HOUR_INTERVAL"
        # result
        return [rVal.strip() for rVal in self._timekprUserConfig[param].split(";") if rVal != ""]

    def getUserPlayTimeEnabled(self):
        """Return whether we have PlayTime enabled"""
        # param
        param = "PLAYTIME_ENABLED"
        # check whether user has this enabled in config
        return self._timekprUserConfig[param]

    def getUserPlayTimeOverrideEnabled(self):
        """Return whether we have PlayTime overrides the normal time accounting"""
        # param
        param = "PLAYTIME_LIMIT_OVERRIDE_ENABLED"
        # result
        return self._timekprUserConfig[param]

    def getUserPlayTimeUnaccountedIntervalsEnabled(self):
        """Return whether PlayTime activities are allowed during unaccounted intervals"""
        # param
        param = "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"
        # result
        return self._timekprUserConfig[param]

    def getUserPlayTimeAllowedWeekdays(self):
        """Get allowed week days for PlayTime"""
        # param
        param = "PLAYTIME_ALLOWED_WEEKDAYS"
        # result
        return [rVal.strip() for rVal in self._timekprUserConfig[param].split(";") if rVal != ""]

    def getUserPlayTimeLimitsPerWeekdays(self):
        """Get allowed limits per week day for PlayTime"""
        # param
        param = "PLAYTIME_LIMITS_PER_WEEKDAYS"
        # result
        return [int(rVal.strip()) for rVal in self._timekprUserConfig[param].split(";") if rVal != ""]

    def getUserPlayTimeActivities(self):
        """Return PlayTime process / process list"""
        # param
        param = "PLAYTIME_ACTIVITIES"
        # result
        return self._timekprUserConfig[param]

    def getUserConfigLastModified(self):
        """Get last file modification time for user"""
        # result
        return datetime.fromtimestamp(os.path.getmtime(self._configFile))

    def setUserAllowedHours(self, pAllowedHours):
        """Set allowed hours"""
        # go through all days given for modifications
        for rDay, rHours in pAllowedHours.items():
            # inital hours
            hours = []

            # go through all hours (in correct order)
            for rHour in range(0, 23+1):
                # convert once
                hour = str(rHour)

                # do we have config for this hour
                if hour in rHours:
                    # is this hour unaccounted
                    unaccounted = "!" if rHours[hour][cons.TK_CTRL_UACC] else ""
                    # do we have proper minuten
                    minutes = ("[%i-%i]" % (rHours[hour][cons.TK_CTRL_SMIN], rHours[hour][cons.TK_CTRL_EMIN])) if (rHours[hour][cons.TK_CTRL_SMIN] > 0 or rHours[hour][cons.TK_CTRL_EMIN] < 60) else ""
                    # build up this hour
                    hours.append("%s%s%s" % (unaccounted, hour, minutes))

            # add this hour to allowable list
            self._timekprUserConfig["ALLOWED_HOURS_%s" % (str(rDay))] = ";".join(hours)

    def setUserAllowedWeekdays(self, pAllowedWeekdays):
        """Set allowed week days"""
        # set up weekdays
        self._timekprUserConfig["ALLOWED_WEEKDAYS"] = ";".join(map(str, pAllowedWeekdays))

    def setUserLimitsPerWeekdays(self, pLimits):
        """Set allowed limits per week day"""
        # set up limits for weekdays
        self._timekprUserConfig["LIMITS_PER_WEEKDAYS"] = ";".join(map(str, pLimits))

    def setUserWeekLimit(self, pWeekLimitSecs):
        """Set limit per week"""
        # set
        self._timekprUserConfig["LIMIT_PER_WEEK"] = int(pWeekLimitSecs)

    def setUserMonthLimit(self, pMonthLimitSecs):
        """Set limit per month"""
        # set
        self._timekprUserConfig["LIMIT_PER_MONTH"] = int(pMonthLimitSecs)

    def setUserTrackInactive(self, pTrackInactive):
        """Set whether to track inactive sessions"""
        # set track inactive
        self._timekprUserConfig["TRACK_INACTIVE"] = pTrackInactive

    def setUserHideTrayIcon(self, pHideTrayIcon):
        """Set whether to hide icon and notifications"""
        # set
        self._timekprUserConfig["HIDE_TRAY_ICON"] = pHideTrayIcon

    def setUserLockoutType(self, pLockoutType):
        """Set user restriction / lockout type"""
        # set
        self._timekprUserConfig["LOCKOUT_TYPE"] = pLockoutType

    def setUserWakeupHourInterval(self, pWakeupHourInterval):
        """Set user wake up hours from / to"""
        # set
        self._timekprUserConfig["WAKEUP_HOUR_INTERVAL"] = ";".join(pWakeupHourInterval)

    def setUserPlayTimeEnabled(self, pPlayTimeEnabled):
        """Set PlayTime enabled for user"""
        # set
        self._timekprUserConfig["PLAYTIME_ENABLED"] = pPlayTimeEnabled

    def setUserPlayTimeOverrideEnabled(self, pPlayTimeOverrideEnabled):
        """Set PlayTime override to the normal time accounting"""
        # set
        self._timekprUserConfig["PLAYTIME_LIMIT_OVERRIDE_ENABLED"] = pPlayTimeOverrideEnabled

    def setUserPlayTimeUnaccountedIntervalsEnabled(self, pPlayTimeUnaccountedIntervalsEnabled):
        """Set whether PlayTime activities are allowed during unaccounted intervals"""
        # set
        self._timekprUserConfig["PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"] = pPlayTimeUnaccountedIntervalsEnabled

    def setUserPlayTimeAllowedWeekdays(self, pPlayTimeAllowedWeekdays):
        """Set allowed week days for PlayTime"""
        # set up weekdays
        self._timekprUserConfig["PLAYTIME_ALLOWED_WEEKDAYS"] = ";".join(map(str, pPlayTimeAllowedWeekdays))

    def setUserPlayTimeLimitsPerWeekdays(self, pPlayTimeAllowedLimitsPerWeekdays):
        """Set allowed week day limits for PlayTime"""
        # set up weekdays
        self._timekprUserConfig["PLAYTIME_LIMITS_PER_WEEKDAYS"] = ";".join(map(str, pPlayTimeAllowedLimitsPerWeekdays))

    def setUserPlayTimeAcitivityList(self, pPlayTimeActivityList):
        """Set PlayTime process / process list"""
        # def
        self._timekprUserConfig["PLAYTIME_ACTIVITIES"] = []
        # loop through all
        for i in range(0, len(pPlayTimeActivityList)):
            # desc
            desc = None if pPlayTimeActivityList[i][1] == "" else pPlayTimeActivityList[i][1]
            # set this up
            self._timekprUserConfig["PLAYTIME_ACTIVITIES"].append([pPlayTimeActivityList[i][0], desc])


class timekprUserControl(object):
    """Class will provide time spent file management functionality"""

    def __init__(self, pDirectory, pUserName):
        """Initialize config"""

        log.log(cons.TK_LOG_LEVEL_INFO, "init user (%s) control" % (pUserName))

        # initialize class variables
        self._configFile = os.path.join(pDirectory, "%s.time" % (pUserName))
        self._userName = pUserName
        self._timekprUserControl = {}

        # config sections from file
        self._timekprUserControl["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init user control")

    def __del__(self):
        """De-initialize config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-init user control")

    def loadUserControl(self, pValidateOnly=False):
        """Read user control config file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading user control (%s)" % (self._userName))

        # directory section
        section = self._userName
        # try to load config file
        result = _loadAndPrepareConfigFile(self._timekprUserControl, self._configFile)
        # value read result
        resultValue = True

        # if we still are fine (and not just checking)
        if not pValidateOnly or (pValidateOnly and result):
            # read config failed, we need to initialize
            if not result:
                # logging
                log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: could not parse the user control file (%s) properly, will recreate" % (self._configFile))
                # init config
                self.initUserControl()
                # re-read the file
                result = _loadAndPrepareConfigFile(self._timekprUserControl, self._configFile)

            # read
            param = "TIME_SPENT_BALANCE"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=0, pCheckValue=cons.TK_LIMIT_PER_DAY, pOverallSuccess=resultValue)
            # read
            param = "TIME_SPENT_DAY"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=0, pCheckValue=cons.TK_LIMIT_PER_DAY, pOverallSuccess=resultValue)
            # read
            param = "TIME_SPENT_WEEK"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=0, pCheckValue=cons.TK_LIMIT_PER_WEEK, pOverallSuccess=resultValue)
            # read
            param = "TIME_SPENT_MONTH"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=0, pCheckValue=cons.TK_LIMIT_PER_MONTH, pOverallSuccess=resultValue)
            # read
            param = "LAST_CHECKED"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=datetime.now().replace(microsecond=0), pCheckValue=None, pOverallSuccess=resultValue)

            # user PlayTime config section
            section = "%s.%s" % (self._userName, "PLAYTIME")
            # read
            param = "PLAYTIME_SPENT_BALANCE"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=0, pCheckValue=cons.TK_LIMIT_PER_DAY, pOverallSuccess=resultValue)
            # read
            param = "PLAYTIME_SPENT_DAY"
            resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(self._timekprUserControl, section, param, pDefaultValue=0, pCheckValue=cons.TK_LIMIT_PER_DAY, pOverallSuccess=resultValue)

            # if we could not read some values, save what we could + defaults
            if not resultValue:
                # logging
                log.log(cons.TK_LOG_LEVEL_WARN, "WARNING: some values in user control file (%s) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead" % (self._configFile))
                # save what we could
                self.initUserControl(True)

        # clear
        self._timekprUserControl["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading user control")

        # result
        return result

    def initUserControl(self, pReuseValues=False):
        """Write new sections of the file"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start init user (%s) control" % (self._userName))

        # build the file
        dstLines = []

        # add new user section
        section = self._userName
        # section
        dstLines += ["[%s]\n" % section]
        dstLines += ["%s\n" % "#### NOTE: all number values are stored in seconds"]
        # set up param
        param = "TIME_SPENT_BALANCE"
        dstLines += ["%s\n" % "# total time balance spent for this day"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserControl[param]) if pReuseValues else "0")]
        # set up param
        param = "TIME_SPENT_DAY"
        dstLines += ["%s\n" % "# total time spent for this day"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserControl[param]) if pReuseValues else "0")]
        # set up param
        param = "TIME_SPENT_WEEK"
        dstLines += ["%s\n" % "# total spent for this week"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserControl[param]) if pReuseValues else "0")]
        # set up param
        param = "TIME_SPENT_MONTH"
        dstLines += ["%s\n" % "# total spent for this month"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserControl[param]) if pReuseValues else "0")]
        # set up param
        param = "LAST_CHECKED"
        dstLines += ["%s\n" % "# last update time of the file"]
        dstLines += ["%s = %s\n" % (param, self._timekprUserControl[param].strftime(cons.TK_DATETIME_FORMAT) if pReuseValues else datetime.now().replace(microsecond=0).strftime(cons.TK_DATETIME_FORMAT))]

        # user PlayTime config section
        section = "%s.%s" % (self._userName, "PLAYTIME")
        # section
        dstLines += ["\n[%s]\n" % section]
        param = "PLAYTIME_SPENT_BALANCE"
        dstLines += ["%s\n" % "# total PlayTime balance spent for this day"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserControl[param]) if pReuseValues else "0")]
        param = "PLAYTIME_SPENT_DAY"
        dstLines += ["%s\n" % "# total PlayTime spent for this day"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprUserControl[param]) if pReuseValues else "0")]

        # write config file
        with open(self._configFile, "w") as dstFile:
            # save the file
            dstFile.writelines(dstLines)

        # clear
        self._timekprUserControl["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init user control")

    def saveControl(self):
        """Save configuration"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start save user (%s) control" % (self._userName))

        # init dict
        values = {}

        # spent day (including bonuses)
        param = "TIME_SPENT_BALANCE"
        values[param] = str(int(self._timekprUserControl[param]))
        # spent day
        param = "TIME_SPENT_DAY"
        values[param] = str(int(self._timekprUserControl[param]))
        # spent week
        param = "TIME_SPENT_WEEK"
        values[param] = str(int(self._timekprUserControl[param]))
        # spent month
        param = "TIME_SPENT_MONTH"
        values[param] = str(int(self._timekprUserControl[param]))
        # last checked
        param = "LAST_CHECKED"
        values[param] = self._timekprUserControl[param].strftime(cons.TK_DATETIME_FORMAT)
        # PlayTime balance
        param = "PLAYTIME_SPENT_BALANCE"
        values[param] = str(int(self._timekprUserControl[param]))
        # PlayTime spent day
        param = "PLAYTIME_SPENT_DAY"
        values[param] = str(int(self._timekprUserControl[param]))

        # edit control file (using alternate method)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish save user control")

    def logUserControl(self):
        """Log user control config file"""
        # log
        log.log(cons.TK_LOG_LEVEL_INFO, "user \"%s\" control:" % (self._userName))

        try:
            # log
            param = "TIME_SPENT_BALANCE"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))
            # log
            param = "TIME_SPENT_DAY"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))
            # log
            param = "TIME_SPENT_WEEK"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))
            # log
            param = "TIME_SPENT_MONTH"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))
            # log
            param = "LAST_CHECKED"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))

            # log
            param = "PLAYTIME_SPENT_BALANCE"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))
            # log
            param = "PLAYTIME_SPENT_DAY"
            log.log(cons.TK_LOG_LEVEL_INFO, "  %s=%s" % (param, str(self._timekprUserControl[param])))
        # fail
        except Exception:
            # log
            log.log(cons.TK_LOG_LEVEL_INFO, "  configuration log failed")

    def getUserDateComponentChanges(self, pCheckDate, pValidationDate=None):
        """Determine whether days / weeks / months changed since last change date in file or other date"""
        # date to validate against
        validationDate = pValidationDate.date() if pValidationDate is not None else self.getUserLastChecked().date()
        checkDate = pCheckDate.date()
        # ## validations ##
        # month changed
        monthChanged = (checkDate.year != validationDate.year or checkDate.month != validationDate.month)
        # week changed
        weekChanged = (checkDate.isocalendar()[1] != validationDate.isocalendar()[1] or (checkDate.isocalendar()[1] == validationDate.isocalendar()[1] and abs((checkDate - validationDate).days) > 7))
        # day changed
        dayChanged = (checkDate != validationDate)

        # result (day / week / month)
        return dayChanged, weekChanged, monthChanged

    def getUserTimeSpentBalance(self):
        """Get time spent for day (including bonues)"""
        # result
        return self._timekprUserControl["TIME_SPENT_BALANCE"]

    def getUserTimeSpentDay(self):
        """Get time spent for day"""
        # result
        return self._timekprUserControl["TIME_SPENT_DAY"]

    def getUserTimeSpentWeek(self):
        """Get time spent for week"""
        # result
        return self._timekprUserControl["TIME_SPENT_WEEK"]

    def getUserTimeSpentMonth(self):
        """Get time spent for month"""
        # result
        return self._timekprUserControl["TIME_SPENT_MONTH"]

    def getUserLastChecked(self):
        """Get last check time for user"""
        # result
        return self._timekprUserControl["LAST_CHECKED"]

    def getUserPlayTimeSpentBalance(self):
        """Get PlayTime balance for day (including bonues)"""
        # result
        return self._timekprUserControl["PLAYTIME_SPENT_BALANCE"]

    def getUserPlayTimeSpentDay(self):
        """Get PlayTime spent for day (including bonues)"""
        # result
        return self._timekprUserControl["PLAYTIME_SPENT_DAY"]

    def getUserControlLastModified(self):
        """Get last file modification time for user"""
        # result
        return datetime.fromtimestamp(os.path.getmtime(self._configFile))

    def setUserTimeSpentBalance(self, pTimeSpent):
        """Set time spent for day (including bonuses)"""
        # set
        self._timekprUserControl["TIME_SPENT_BALANCE"] = pTimeSpent

    def setUserTimeSpentDay(self, pTimeSpentDay):
        """Set time spent for day"""
        # set
        self._timekprUserControl["TIME_SPENT_DAY"] = pTimeSpentDay

    def setUserTimeSpentWeek(self, pTimeSpentWeek):
        """Set time spent for week"""
        # set
        self._timekprUserControl["TIME_SPENT_WEEK"] = pTimeSpentWeek

    def setUserTimeSpentMonth(self, pTimeSpentMonth):
        """Set time spent for month"""
        # set
        self._timekprUserControl["TIME_SPENT_MONTH"] = pTimeSpentMonth

    def setUserLastChecked(self, pEffectiveDatetime):
        """Set last check time for user"""
        # set
        self._timekprUserControl["LAST_CHECKED"] = pEffectiveDatetime

    def setUserPlayTimeSpentBalance(self, pTimeSpent):
        """Set PlayTime balance for day (including bonues)"""
        # set
        self._timekprUserControl["PLAYTIME_SPENT_BALANCE"] = pTimeSpent

    def setUserPlayTimeSpentDay(self, pTimeSpent):
        """Set PlayTime spent for day (including bonues)"""
        # set
        self._timekprUserControl["PLAYTIME_SPENT_DAY"] = pTimeSpent

class timekprClientConfig(object):
    """Class will hold and provide config management for user"""

    def __init__(self):
        """Initialize config"""
        # config
        self._timekprClientConfig = {}
        # config sections from file
        self._timekprClientConfig["CONFIGLOADER"] = {}
        # get home
        self._userHome = os.path.expanduser("~")

        # set up log file name
        self._timekprClientConfig["TIMEKPR_LOGFILE_DIR"] = cons.TK_LOG_TEMP_DIR

        log.log(cons.TK_LOG_LEVEL_INFO, "start initializing client configuration manager")

        # in dev
        self._configDirPrefix = os.getcwd() if cons.TK_DEV_ACTIVE else ""

        # main config
        self._timekprClientConfig["TIMEKPR_MAIN_CONFIG_DIR"] = os.path.join(self._configDirPrefix, (cons.TK_MAIN_CONFIG_DIR_DEV if cons.TK_DEV_ACTIVE else cons.TK_MAIN_CONFIG_DIR))
        self._configMainFile = os.path.join(self._timekprClientConfig["TIMEKPR_MAIN_CONFIG_DIR"], cons.TK_MAIN_CONFIG_FILE)

        # config
        self._configFile = os.path.join(self._userHome, ".config/timekpr", cons.TK_MAIN_CONFIG_FILE)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish initializing client configuration manager")

    def __del__(self):
        """De-initialize config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-initialize client configuration manager")

    def loadClientConfiguration(self):
        """Read main timekpr config file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading client configuration")

        # get directories from main config
        if "TIMEKPR_SHARED_DIR" not in self._timekprClientConfig:
            # load main config to get directories
            self.loadMinimalClientMainConfig()
            # clear out cp, we don't need to store all condfigs in minimal case
            self._timekprClientConfig["CONFIGLOADER"] = {}

        # try to load config file
        result = _loadAndPrepareConfigFile(self._timekprClientConfig, self._configFile)

        # value read result
        resultValue = True

        # read config failed, we need to initialize
        if not result:
            # logging
            log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: could not parse the configuration file (%s) properly, will use default values" % (self._configFile))
            # write correct config file
            self.initClientConfig()
            # re-read the file
            result = _loadAndPrepareConfigFile(self._timekprClientConfig, self._configFile)

        # config load time
        self._clientConfigModTime = self.getClientLastModified()

        # directory section
        section = "CONFIG"
        # read
        param = "LOG_LEVEL"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=cons.TK_USR_LOG_LEVEL_INFO, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "SHOW_LIMIT_NOTIFICATION"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=True, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "SHOW_ALL_NOTIFICATIONS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=True, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "USE_SPEECH_NOTIFICATIONS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=False, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "SHOW_SECONDS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=False, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "NOTIFICATION_TIMEOUT"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=cons.TK_CL_NOTIF_TMO, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "NOTIFICATION_TIMEOUT_CRITICAL"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=cons.TK_CL_NOTIF_CRIT_TMO, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "USE_NOTIFICATION_SOUNDS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=False, pCheckValue=None, pOverallSuccess=resultValue)
        # read
        param = "NOTIFICATION_LEVELS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=cons.TK_NOTIFICATION_LEVELS, pCheckValue=None, pOverallSuccess=resultValue)
        self._timekprClientConfig[param] = _cleanupValue(self._timekprClientConfig[param])
        # read
        param = "PLAYTIME_NOTIFICATION_LEVELS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=cons.TK_PT_NOTIFICATION_LEVELS, pCheckValue=None, pOverallSuccess=resultValue)
        self._timekprClientConfig[param] = _cleanupValue(self._timekprClientConfig[param])

        # if we could not read some values, save what we could + defaults
        if not resultValue:
            # logging
            log.log(cons.TK_LOG_LEVEL_WARN, "WARNING: some values in client confguration file (%s) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead" % (self._configFile))
            # save what we could
            self.initClientConfig(True)

        # check whether sound is supported
        if cons.TK_CL_NOTIF_SND_TYPE == "sound-name":
            self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"] = True
        else:
            self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"] = (os.path.isfile(cons.TK_CL_NOTIF_SND_FILE_WARN) and os.path.isfile(cons.TK_CL_NOTIF_SND_FILE_CRITICAL))

        # check whether speech is supported
        try:
            # try importing speech
            import timekpr.client.interface.speech.espeak as espeak
            # supported
            self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS_SUPPORTED"] =  espeak.isSupported()
        except:
            # NOT supported
            self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS_SUPPORTED"] = False

        # clear
        self._timekprClientConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading client configuration")

        # result
        return True

    def loadMinimalClientMainConfig(self):
        """Load main configuration file to get shared file locations"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading minimal main configuration")
        # defaults
        # directory section
        section = "DIRECTORIES"
        # read
        param = "TIMEKPR_SHARED_DIR"

        # try to load config file
        result = _loadAndPrepareConfigFile(self._timekprClientConfig, self._configMainFile, True)
        # if file cannot be read
        if not result:
            # default value
            value = cons.TK_SHARED_DIR
        else:
            # read file
            result, value = _readAndNormalizeValue(self._timekprClientConfig, section, param, pDefaultValue=cons.TK_SHARED_DIR, pCheckValue=None, pOverallSuccess=True)

        # problems loading default config
        if not result:
            # logging
            log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: could not parse the configuration file (%s) properly, will use default values" % (self._configMainFile))

        # finalize directory
        self._timekprClientConfig[param] = os.path.join(self._configDirPrefix, (cons.TK_SHARED_DIR_DEV if cons.TK_DEV_ACTIVE else value))

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading minimal main configuration")

        # result
        return True

    def initClientConfig(self, pReuseValues=False):
        """Write new config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start init client configuration")

        # build the file
        dstLines = []

        # add new user section
        section = "DOCUMENTATION"
        # section
        dstLines += ["[%s]\n" % section]
        dstLines += ["%s\n" % "# client application configuration file"]
        dstLines += ["%s\n" % "# NOTE: this file is not intended to be edited manually, however, if it is, please restart the application"]

        # add new user section
        section = "CONFIG"
        # section
        dstLines += ["\n[%s]\n" % section]
        # set up param
        param = "LOG_LEVEL"
        dstLines += ["%s\n" % "# user logging level (1 - normal, 2 - debug, 3 - extra debug)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else str(cons.TK_USR_LOG_LEVEL_INFO))]
        # set up param
        param = "SHOW_LIMIT_NOTIFICATION"
        dstLines += ["%s\n" % "# whether to show limit change notification"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else "True")]
        # set up param
        param = "SHOW_ALL_NOTIFICATIONS"
        dstLines += ["%s\n" % "# whether to show all notifications or important ones only"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else "True")]
        # set up param
        param = "SHOW_SECONDS"
        dstLines += ["%s\n" % "# whether to show seconds in label (if DE supports it)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else "True")]
        # set up param
        param = "USE_SPEECH_NOTIFICATIONS"
        dstLines += ["%s\n" % "# whether to use speech notifications"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else str(cons.TK_TRACK_INACTIVE))]
        # set up param
        param = "NOTIFICATION_TIMEOUT"
        dstLines += ["%s\n" % "# how long regular notifications should be displayed (in seconds)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else str(cons.TK_CL_NOTIF_TMO))]
        # set up param
        param = "NOTIFICATION_TIMEOUT_CRITICAL"
        dstLines += ["%s\n" % "# how long critical notifications should be displayed (in seconds)"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else str(cons.TK_CL_NOTIF_CRIT_TMO))]
        # set up param
        param = "USE_NOTIFICATION_SOUNDS"
        dstLines += ["%s\n" % "# use notification sounds for notifications"]
        dstLines += ["%s = %s\n" % (param, str(self._timekprClientConfig[param]) if pReuseValues else str(cons.TK_TRACK_INACTIVE))]
        # set up param
        param = "NOTIFICATION_LEVELS"
        dstLines += ["%s\n" % "# user configured notification levels in form of level[priority];..."]
        dstLines += ["%s = %s\n" % (param, self._timekprClientConfig[param] if pReuseValues else cons.TK_NOTIFICATION_LEVELS)]
        # set up param
        param = "PLAYTIME_NOTIFICATION_LEVELS"
        dstLines += ["%s\n" % "# user configured PlayTime notification levels in form of level[priority];..."]
        dstLines += ["%s = %s\n" % (param, self._timekprClientConfig[param] if pReuseValues else cons.TK_PT_NOTIFICATION_LEVELS)]

        # directory name
        dirName = os.path.dirname(self._configFile)

        # check file
        if not os.path.isdir(dirName):
            # make it
            os.makedirs(dirName)

        # write config file
        with open(self._configFile, "w") as dstFile:
            # save the file
            dstFile.writelines(dstLines)

        # clear
        self._timekprClientConfig["CONFIGLOADER"] = {}

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init client configuration")

    def saveClientConfig(self):
        """Save configuration (called from GUI)"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start save client config")

        # init dict
        values = {}

        # log level
        param = "LOG_LEVEL"
        values[param] = str(self._timekprClientConfig[param])
        # first limit notification
        param = "SHOW_LIMIT_NOTIFICATION"
        values[param] = str(self._timekprClientConfig[param])
        # all notifications
        param = "SHOW_ALL_NOTIFICATIONS"
        values[param] = str(self._timekprClientConfig[param])
        # speech notifications
        param = "USE_SPEECH_NOTIFICATIONS"
        values[param] = str(self._timekprClientConfig[param])
        # show seconds
        param = "SHOW_SECONDS"
        values[param] = str(self._timekprClientConfig[param])
        # timeout for notifications
        param = "NOTIFICATION_TIMEOUT"
        values[param] = str(self._timekprClientConfig[param])
        # timeout for critical notifications
        param = "NOTIFICATION_TIMEOUT_CRITICAL"
        values[param] = str(self._timekprClientConfig[param])
        # notification sounds
        param = "USE_NOTIFICATION_SOUNDS"
        values[param] = str(self._timekprClientConfig[param])
        # notification levels
        param = "NOTIFICATION_LEVELS"
        values[param] = self._timekprClientConfig[param]
        # PlayTime notification levels
        param = "PLAYTIME_NOTIFICATION_LEVELS"
        values[param] = self._timekprClientConfig[param]

        # edit control file (using alternate method)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish save client config")

    def isClientConfigChanged(self):
        """Whether config has changed"""
        # defaults
        result = False
        clientLastModified = self.getClientLastModified()

        # yes, is it changed?
        if self._clientConfigModTime != clientLastModified:
            log.log(cons.TK_LOG_LEVEL_INFO, "client config changed, prev/now: %s / %s" % (self._clientConfigModTime.strftime(cons.TK_LOG_DATETIME_FORMAT), clientLastModified.strftime(cons.TK_LOG_DATETIME_FORMAT)))
            # changed
            self._clientConfigModTime = clientLastModified
            # load config
            self.loadClientConfiguration()
            # changed
            result = True

        # result
        return result

    def _parseNotificationLevels(self, pKey):
        """Parse notification levels, if can not be parsed, return None"""
        # def
        result = []
        # work on levels
        for rLvl in self._timekprClientConfig[pKey].split(";"):
            # no need for non-empty values
            if rLvl != "":
                # try to find time left and level
                secs, prio = splitConfigValueNameParam(rLvl)
                # if identified correctly (e.g. we have secs and level too)
                if secs is not None and prio is not None:
                    # this is just to verify that config is OK
                    if prio in cons.TK_PRIO_LVL_MAP:
                        # add to list
                        result.append([int(secs), prio])
        # result
        return result

    def _formatClientNotificationLevels(self, pNotificationLevels):
        """Get formatted notification levels"""
        # def
        result = ""
        # loop through settings
        for rPrio in pNotificationLevels:
            # levels should be sorted from higher limit to lower
            result = "%s%s%s" % (result, ("" if result == "" else ";"), "%s[%s]" % (str(rPrio[0]), str(rPrio[1])))
        # result
        return result

    def getIsNotificationSoundSupported(self):
        """Whether notification sounds are supported"""
        # result
        return self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"]

    def getIsNotificationSpeechSupported(self):
        """Whether speech notifications are supported"""
        # result
        return self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS_SUPPORTED"]

    def getClientShowLimitNotifications(self):
        """Get whether to show frst notification"""
        # result
        return self._timekprClientConfig["SHOW_LIMIT_NOTIFICATION"]

    def getClientShowAllNotifications(self):
        """Get whether to show all notifications"""
        # result
        return self._timekprClientConfig["SHOW_ALL_NOTIFICATIONS"]

    def getClientUseSpeechNotifications(self):
        """Get whether to use speech"""
        # result
        return self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS"]

    def getClientShowSeconds(self):
        """Get whether to show seconds"""
        # result
        return self._timekprClientConfig["SHOW_SECONDS"]

    def getClientNotificationTimeout(self):
        """Get timeout for regular notifications"""
        # result
        return self._timekprClientConfig["NOTIFICATION_TIMEOUT"]

    def getClientNotificationTimeoutCritical(self):
        """Get timeout for critical notifications"""
        # result
        return self._timekprClientConfig["NOTIFICATION_TIMEOUT_CRITICAL"]

    def getClientUseNotificationSound(self):
        """Get whether to show use sound notifications"""
        # result
        return self._timekprClientConfig["USE_NOTIFICATION_SOUNDS"]

    def getClientNotificationLevels(self):
        """Get notification levels"""
        # result
        return self._parseNotificationLevels("NOTIFICATION_LEVELS")

    def getClientPlayTimeNotificationLevels(self):
        """Get PlayTime notification levels"""
        # result
        return self._parseNotificationLevels("PLAYTIME_NOTIFICATION_LEVELS")

    def getClientLogLevel(self):
        """Get client log level"""
        # result
        return self._timekprClientConfig["LOG_LEVEL"]

    def getTimekprSharedDir(self):
        """Get shared dir"""
        # result
        return self._timekprClientConfig["TIMEKPR_SHARED_DIR"]

    def getClientLogfileDir(self):
        """Get shared dir"""
        # result
        return self._timekprClientConfig["TIMEKPR_LOGFILE_DIR"]

    def getClientLastModified(self):
        """Get last file modification time for user"""
        # result
        return datetime.fromtimestamp(os.path.getmtime(self._configFile))

    def setClientLogLevel(self, pClientLogLevel):
        """Set client log level"""
        # set
        self._timekprClientConfig["LOG_LEVEL"] = pClientLogLevel

    def setIsNotificationSoundSupported(self, pIsSupported):
        """Whether notification sounds are supported"""
        # set
        self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"] = pIsSupported

    def setClientShowLimitNotifications(self, pClientShowLimitNotification):
        """Set whether to show frst notification"""
        # set
        self._timekprClientConfig["SHOW_LIMIT_NOTIFICATION"] = pClientShowLimitNotification

    def setClientShowAllNotifications(self, pClientShowAllNotifications):
        """Set whether to show all notifications"""
        # set
        self._timekprClientConfig["SHOW_ALL_NOTIFICATIONS"] = pClientShowAllNotifications

    def setClientUseSpeechNotifications(self, pClientUseSpeechNotifications):
        """Set whether to use speech"""
        # set
        self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS"] = pClientUseSpeechNotifications

    def setClientShowSeconds(self, pClientShowSeconds):
        """Set whether to show seconds"""
        # set
        self._timekprClientConfig["SHOW_SECONDS"] = pClientShowSeconds

    def setClientNotificationTimeout(self, pClientNotificationTimeout):
        """Set timeout for regular notifications"""
        # set
        self._timekprClientConfig["NOTIFICATION_TIMEOUT"] = pClientNotificationTimeout

    def setClientNotificationTimeoutCritical(self, pClientNotificationTimeoutCritical):
        """Set timeout for critical notifications"""
        # set
        self._timekprClientConfig["NOTIFICATION_TIMEOUT_CRITICAL"] = pClientNotificationTimeoutCritical

    def setClientUseNotificationSound(self, pClientUseNotificationSound):
        """Set whether to use sound notifications"""
        # set
        self._timekprClientConfig["USE_NOTIFICATION_SOUNDS"] = pClientUseNotificationSound

    def setClientNotificationLevels(self, pNotificationLevels):
        """Set whether to use sound notifications"""
        # set
        self._timekprClientConfig["NOTIFICATION_LEVELS"] = self._formatClientNotificationLevels(pNotificationLevels)

    def setClientPlayTimeNotificationLevels(self, pPlayTimeNotificationLevels):
        """Set whether to use sound notifications"""
        # set
        self._timekprClientConfig["PLAYTIME_NOTIFICATION_LEVELS"] = self._formatClientNotificationLevels(pPlayTimeNotificationLevels)
