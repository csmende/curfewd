"""
Created on Aug 28, 2018

@author: mjasnik
"""

# imports
import os
import getpass
from os import geteuid

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.client.interface.dbus.administration import timekprAdminConnector
from timekpr.common.utils.config import timekprConfig
from timekpr.common.constants import messages as msg
from timekpr.common.utils.misc import findHourStartEndMinutes as findHourStartEndMinutes
from timekpr.common.utils.misc import splitConfigValueNameParam as splitConfigValueNameParam
from timekpr.common.utils.misc import getSecondsFromTimeStr
from timekpr.common.utils.misc import getTimeStrFromSeconds


class timekprAdminClient(object):
    """Main class for holding all client logic (including dbus)"""

    # --------------- initialization / control methods --------------- #

    def __init__(self):
        """Initialize admin client"""
        # get our connector
        self._timekprAdminConnector = timekprAdminConnector()

        # main object for GUI
        self._adminGUI = None

    def startTimekprAdminClient(self, *args):
        """Start up timekpr admin (choose gui or cli and start this up)"""
        # def
        result = 0
        # check whether we need CLI or GUI
        lastParam = args[len(args)-1]
        timekprForceCLI = False

        # configuration init
        _timekprConfig = timekprConfig()
        # load config
        _timekprConfig.loadMainConfiguration()

        # init logging
        log.setLogging(_timekprConfig.getTimekprLogLevel(), cons.TK_LOG_TEMP_DIR, (cons.TK_LOG_OWNER_ADMIN_SU if geteuid() == 0 else cons.TK_LOG_OWNER_ADMIN), getpass.getuser())

        # check for script
        if ("/timekpra" in lastParam or "timekpra.py" in lastParam):
            # whether we have X running or wayland?
            timekprX11Available = os.getenv("DISPLAY") is not None
            timekprWaylandAvailable = os.getenv("WAYLAND_DISPLAY") is not None
            timekprMirAvailable = os.getenv("MIR_SOCKET") is not None

            # if we are required to run graphical thing
            if (timekprX11Available or timekprWaylandAvailable or timekprMirAvailable):
                # resource dir
                _resourcePathGUI = os.path.join(_timekprConfig.getTimekprSharedDir(), "client/forms")
                # use GUI
                from timekpr.client.gui.admingui import timekprAdminGUI
                # load GUI and process from there
                self._adminGUI = timekprAdminGUI(cons.TK_VERSION, _resourcePathGUI, getpass.getuser())
                # start GUI
                self._adminGUI.startAdminGUI()
            # nor X nor wayland are available
            else:
                # print to console
                log.consoleOut("%s\n" % (msg.getTranslation("TK_MSG_CONSOLE_GUI_NOT_AVAILABLE")))
                # forced CLI"
                timekprForceCLI = True
        else:
            # CLI
            timekprForceCLI = True

        # for CLI connections
        if timekprForceCLI:
            # connect
            self._timekprAdminConnector.initTimekprConnection(True, False, timekprForceCLI)
            # connected?
            if self._timekprAdminConnector.isConnected()[1]:
                # use CLI
                # validate possible parameters and their values, when fine - execute them as well
                result = self.checkAndExecuteAdminCommands(*args)
                log.autoFlushLogFile(True)

        # ret
        return result

    # --------------- initialization / helper methods --------------- #

    def finishTimekpr(self, signal=None, frame=None):
        """Exit timekpr admin GUI gracefully"""
        if self._adminGUI is not None:
            # finish main thread on GUI`
            self._adminGUI.finishTimekpr(signal, frame)

    # --------------- parameter validation methods --------------- #

    def checkAndExecuteAdminCommands(self, *args):
        """Init connection to timekpr dbus server"""
        # initial param len
        paramIdx = 0
        paramLen = len(args)
        adminCmdIncorrect = False
        tmpIdx = 0
        result = 0

        # determine parameter offset
        for rArg in args:
            # count offset
            tmpIdx += 1
            # check for script
            if "/timekpra" in rArg or "timekpra.py" in rArg:
                paramIdx = tmpIdx

        # this gets the command itself (args[0] is the script name)
        adminCmd = args[paramIdx] if paramLen > paramIdx else "timekpra"

        # now based on params check them out
        # this gets saved user list from the server
        if adminCmd == "--help":
            # fine
            pass
        # this gets saved user list from the server
        elif adminCmd in ("--getuserlist", "--userlist"):
            # check param len
            if paramLen != paramIdx + 1:
                # fail
                adminCmdIncorrect = True
            else:
                # get list
                result, message, userList = self._timekprAdminConnector.getUserList()

                # process
                if result == 0:
                    # process
                    result, message = self.printUserList(userList)
        # this gets user configuration from the server
        elif adminCmd in ("--getuserinfo", "--userinfo"):
            # check param len
            if not (paramLen >= paramIdx + 2 and paramLen <= paramIdx + 3):
                # fail
                adminCmdIncorrect = True
            else:
                # get user config
                result, message, userConfig = self._timekprAdminConnector.getUserConfigurationAndInformation(args[paramIdx + 1], cons.TK_CL_INF_FULL)

                # process
                if result == 0:
                    # process
                    result, message = self.printUserConfig(args[paramIdx + 1], userConfig, True if paramLen > paramIdx + 2 and args[paramIdx + 2] == "-h" else False)
        # this gets user configuration from the server
        elif adminCmd in ("--getuserinfort", "--userinfort"):
            # check param len
            if not (paramLen >= paramIdx + 2 and paramLen <= paramIdx + 3):
                # fail
                adminCmdIncorrect = True
            else:
                # get user config
                result, message, userConfig = self._timekprAdminConnector.getUserConfigurationAndInformation(args[paramIdx + 1], cons.TK_CL_INF_RT)

                # process
                if result == 0:
                    # process
                    result, message = self.printUserConfig(args[paramIdx + 1], userConfig, True if paramLen > paramIdx + 2 and args[paramIdx + 2] == "-h" else False)
        # this sets allowed days for the user
        elif adminCmd == "--setalloweddays":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetAllowedDays(args[paramIdx + 1], args[paramIdx + 2])
        # this sets allowed hours per specified day or ALL for every day
        elif adminCmd == "--setallowedhours":
            # check param len
            if paramLen != paramIdx + 4:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetAllowedHours(args[paramIdx + 1], args[paramIdx + 2], args[paramIdx + 3])
        # this sets time limits per allowed days
        elif adminCmd == "--settimelimits":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetTimeLimits(args[paramIdx + 1], args[paramIdx + 2])
        # this sets time limits per week
        elif adminCmd == "--settimelimitweek":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetTimeLimitWeek(args[paramIdx + 1], args[paramIdx + 2])
        # this sets time limits per month
        elif adminCmd == "--settimelimitmonth":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetTimeLimitMonth(args[paramIdx + 1], args[paramIdx + 2])
        # this sets whether to track inactive user sessions
        elif adminCmd == "--settrackinactive":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetTrackInactive(args[paramIdx + 1], args[paramIdx + 2])
        # this sets whether to show tray icon
        elif adminCmd == "--sethidetrayicon":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetHideTrayIcon(args[paramIdx + 1], args[paramIdx + 2])
        # this sets lockout type for the user
        elif adminCmd == "--setlockouttype":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetLockoutType(args[paramIdx + 1], args[paramIdx + 2])
        # this sets whether PlayTime is enabled for user
        elif adminCmd == "--setplaytimeenabled":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeEnabled(args[paramIdx + 1], args[paramIdx + 2])
        # this sets playtime override for user
        elif adminCmd == "--setplaytimelimitoverride":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeLimitOverride(args[paramIdx + 1], args[paramIdx + 2])
        # this sets playtime allowed during unaccounted intervals for user
        elif adminCmd == "--setplaytimeunaccountedintervalsflag":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeUnaccountedIntervalsEnabled(args[paramIdx + 1], args[paramIdx + 2])
        # this sets allowed days for PlayTime for the user
        elif adminCmd == "--setplaytimealloweddays":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeAllowedDays(args[paramIdx + 1], args[paramIdx + 2])
        # this sets PlayTime limits for allowed days for the user
        elif adminCmd == "--setplaytimelimits":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeLimits(args[paramIdx + 1], args[paramIdx + 2])
        # this sets PlayTime activities for the user
        elif adminCmd == "--setplaytimeactivities":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeActivities(args[paramIdx + 1], args[paramIdx + 2])
        # this sets time left for the user at current moment
        elif adminCmd == "--settimeleft":
            # check param len
            if paramLen != paramIdx + 4:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetTimeLeft(args[paramIdx + 1], args[paramIdx + 2], args[paramIdx + 3])
        # this sets time left for the user at current moment
        elif adminCmd == "--setplaytimeleft":
            # check param len
            if paramLen != paramIdx + 4:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                result, message = self.processSetPlayTimeLeft(args[paramIdx + 1], args[paramIdx + 2], args[paramIdx + 3])
        else:
            # out
            adminCmdIncorrect = True

        # err
        if result != 0:
            # log error
            log.consoleOut(message)

        # initial order as well as sibling cmds
        cmds = ["--help", "--getuserlist", "--userlist", "--getuserinfo", "--userinfo", "--getuserinfort", "--userinfort"]

        # check whether command is supported
        if ((adminCmd not in cons.TK_USER_ADMIN_COMMANDS and adminCmd not in cons.TK_ADMIN_COMMANDS and adminCmd not in cmds)
            or adminCmd == "--help"
            or adminCmdIncorrect
        ):
            # fail
            if adminCmdIncorrect:
                # fail msg
                log.consoleOut(msg.getTranslation("TK_MSG_CONSOLE_COMMAND_INCORRECT"), *args, "\n")

            # log notice
            log.consoleOut("%s\n*) %s\n*) %s\n*) %s\n" % (
                msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_HEAD"),
                msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_TIME"),
                msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_HOURS"),
                msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_DAYS"))
            )
            # log usage notes text
            log.consoleOut("%s\n" % (msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTES")))

            # print initial commands as first
            for rCmd in cmds:
                # if exists (this needs to be enabled if there are multiple cmds to do the same thing)
                if rCmd in cons.TK_USER_ADMIN_COMMANDS:
                    # log on screen
                    log.consoleOut(" ", rCmd, cons.TK_USER_ADMIN_COMMANDS[rCmd], "\n")

            # print help
            for rCmd, rCmdDesc in cons.TK_USER_ADMIN_COMMANDS.items():
                # do not print already known commands
                if rCmd not in cmds:
                    # log on screen
                    log.consoleOut(" ", rCmd, rCmdDesc, "\n")

        # fin
        return result

    # --------------- parameter execution methods --------------- #

    def printUserList(self, pUserList):
        """Format and print userlist"""
        # print to console
        log.consoleOut(msg.getTranslation("TK_MSG_CONSOLE_USERS_TOTAL", len(pUserList)))
        # loop and print
        for rUser in pUserList:
            log.consoleOut(rUser[0])
        # ret
        return 0, ""

    def printUserConfig(self, pUserName, pPrintUserConfig, pEasyTime=False):
        """Format and print user config"""
        # print to console
        log.consoleOut("# %s" % (msg.getTranslation("TK_MSG_CONSOLE_CONFIG_FOR") % (pUserName)))

        # loop and print the same format as ppl will use to set that
        for rUserKey, rUserConfig in pPrintUserConfig.items():
            # final value
            result = ""

            # join the lists for days
            if rUserKey in ("ALLOWED_WEEKDAYS", "PLAYTIME_ALLOWED_WEEKDAYS"):
                # get final value
                result = ";".join(str(rVal) for rVal in rUserConfig)
            # join the lists for limits
            elif rUserKey in ("LIMITS_PER_WEEKDAYS", "PLAYTIME_LIMITS_PER_WEEKDAYS"):
                # get final value
                result = ";".join(getTimeStrFromSeconds(int(rVal)) if pEasyTime else str(rVal) for rVal in rUserConfig)
            # join the lists for days / hours
            elif "ALLOWED_HOURS_" in rUserKey:
                # print join
                if len(rUserConfig) > 0:
                    # process hours
                    for rUserHour in sorted(list(map(int, rUserConfig))):
                        # unaccounted hour
                        uacc = "!" if rUserConfig[str(rUserHour)][cons.TK_CTRL_UACC] else ""
                        # get config per hr
                        hr = "%s" % (rUserHour) if rUserConfig[str(rUserHour)][cons.TK_CTRL_SMIN] <= 0 and rUserConfig[str(rUserHour)][cons.TK_CTRL_EMIN] >= 60 else "%s[%s-%s]" % (rUserHour, rUserConfig[str(rUserHour)][cons.TK_CTRL_SMIN], rUserConfig[str(rUserHour)][cons.TK_CTRL_EMIN])
                        # empty
                        result = "%s%s" % (uacc, hr) if result == "" else "%s;%s%s" % (result, uacc, hr)
            # bools
            elif rUserKey in ("TRACK_INACTIVE", "HIDE_TRAY_ICON", "PLAYTIME_ENABLED", "PLAYTIME_LIMIT_OVERRIDE_ENABLED", "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"):
                # bools
                result = True if str(bool(rUserConfig)).lower() == "true" else False
            # strings
            elif rUserKey in ("PLAYTIME_ACTIVITIES"):
                # loop thorhough activities
                for rActArr in rUserConfig:
                    # activity
                    act = "%s[%s]" % (rActArr[0], rActArr[1]) if rActArr[1] != "" else "%s" % (rActArr[0])
                    # gather activities
                    result = "%s" % (act) if result == "" else "%s;%s" % (result, act)
            # limits
            elif rUserKey in ("LIMIT_PER_WEEK" ,"LIMIT_PER_MONTH" ,"TIME_SPENT_BALANCE" ,"TIME_SPENT_DAY" ,"TIME_SPENT_WEEK" ,"TIME_SPENT_MONTH" ,"TIME_LEFT_DAY" ,"PLAYTIME_LEFT_DAY" ,"PLAYTIME_SPENT_DAY" ,"ACTUAL_TIME_SPENT_SESSION" ,"ACTUAL_TIME_INACTIVE_SESSION" ,"ACTUAL_TIME_SPENT_BALANCE " ,"ACTUAL_TIME_SPENT_DAY" ,"ACTUAL_TIME_LEFT_DAY" ,"ACTUAL_TIME_LEFT_CONTINUOUS" ,"ACTUAL_PLAYTIME_LEFT_DAY"):
                # final
                result = getTimeStrFromSeconds(int(rUserConfig)) if pEasyTime else str(rUserConfig)
            # the rest
            else:
                # final val
                result = str(rUserConfig)

            # print final
            log.consoleOut("%s: %s" % (rUserKey, result))

        # ret
        return 0, ""

    def processSetAllowedDays(self, pUserName, pDayList):
        """Process allowed days"""
        # defaults
        dayMap = []
        result = 0
        message = ""

        # day map
        try:
            # try to parse parameters
            dayMap = str(pDayList).split(";")
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setAllowedDays(pUserName, dayMap)

        # ret
        return result, message

    def processSetAllowedHours(self, pUserName, pDayNumber, pHourList):
        """Process allowed hours"""
        # this is the dict for hour config
        allowedHours = {}
        result = 0
        message = ""

        # allowed hours
        try:
            # check hours
            for rHour in str(pHourList).split(";"):
                # get hours and minutes
                hour, sMin, eMin, uacc = findHourStartEndMinutes(rHour)
                # raise any error in case we can not get parsing right
                if hour is None:
                    # raise
                    raise ValueError("this does not compute")
                # set hours
                allowedHours[str(hour)] = {cons.TK_CTRL_SMIN: sMin, cons.TK_CTRL_EMIN: eMin, cons.TK_CTRL_UACC: uacc}
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setAllowedHours(pUserName, pDayNumber, allowedHours)

        # ret
        return result, message

    def processSetTimeLimits(self, pUserName, pDayLimits):
        """Process time limits for days"""
        # defaults
        dayLimits = []
        result = 0
        message = ""

        # day limits
        try:
            # allow empty limits too
            if str(pDayLimits) != "":
                # convert limits
                for rDl in pDayLimits.split(";"):
                    # limit
                    limit = getSecondsFromTimeStr(rDl)
                    # err
                    if limit is None:
                        raise ValueError("%s is not a number or timestring" % (rDl))
                    else:
                        limit = int(limit)
                    # add limit
                    dayLimits += [limit]
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLimitForDays(pUserName, dayLimits)

        # ret
        return result, message

    def processSetTimeLimitWeek(self, pUserName, pTimeLimitWeek):
        """Process time limits for week"""
        # defaults
        weekLimit = 0
        result = 0
        message = ""

        # week limit
        try:
            # try to parse parameters
            weekLimit = getSecondsFromTimeStr(pTimeLimitWeek)
            # err
            if weekLimit is None:
                raise ValueError("%s is not a number or timestring" % (pTimeLimitWeek))
            else:
                weekLimit = int(weekLimit)
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLimitForWeek(pUserName, weekLimit)

        # ret
        return result, message

    def processSetTimeLimitMonth(self, pUserName, pTimeLimitMonth):
        """Process time limits for month"""
        # defaults
        monthLimit = 0
        result = 0
        message = ""

        # week limit
        try:
            # try to parse parameters
            monthLimit = getSecondsFromTimeStr(pTimeLimitMonth)
            # err
            if monthLimit is None:
                raise ValueError("%s is not a number or timestring" % (pTimeLimitMonth))
            else:
                monthLimit = int(monthLimit)
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLimitForMonth(pUserName, monthLimit)

        # ret
        return result, message

    def processSetTrackInactive(self, pUserName, pTrackInactive):
        """Process track inactive"""
        # defaults
        trackInactive = None
        result = 0
        message = ""

        # check
        if str(pTrackInactive).lower() not in ("true", "false"):
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= ("please specify true or false")
        else:
            trackInactive = True if str(pTrackInactive).lower() == "true" else False

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTrackInactive(pUserName, trackInactive)

        # ret
        return result, message

    def processSetHideTrayIcon(self, pUserName, pHideTrayIcon):
        """Process hide tray icon"""
        # defaults
        hideTrayIcon = None
        result = 0
        message = ""

        # check
        if str(pHideTrayIcon).lower() not in ("true", "false"):
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= ("please specify true or false")
        else:
            hideTrayIcon = True if str(pHideTrayIcon).lower() == "true" else False

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setHideTrayIcon(pUserName, hideTrayIcon)

        # ret
        return result, message

    def processSetLockoutType(self, pUserName, pLockoutType):
        """Process lockout type"""
        # defaults
        result = 0
        message = ""
        # parse lockout
        lockout = str(pLockoutType).split(";")
        lockoutType = lockout[0]
        lockoutWakeFrom = lockout[1] if len(lockout) == 3 else '0'
        lockoutWakeTo = lockout[2] if len(lockout) == 3 else '23'

        # check
        if lockoutType not in (cons.TK_CTRL_RES_L, cons.TK_CTRL_RES_S, cons.TK_CTRL_RES_W, cons.TK_CTRL_RES_T, cons.TK_CTRL_RES_K, cons.TK_CTRL_RES_D):
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= ("please specify one of these: %s, %s, %s, %s, %s, %s" % (cons.TK_CTRL_RES_L, cons.TK_CTRL_RES_S, cons.TK_CTRL_RES_W, cons.TK_CTRL_RES_T, cons.TK_CTRL_RES_K, cons.TK_CTRL_RES_D))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setLockoutType(pUserName, lockoutType, lockoutWakeFrom, lockoutWakeTo)

        # ret
        return result, message

    def processSetTimeLeft(self, pUserName, pOperation, pLimit):
        """Process time left"""
        # defaults
        limit = 0
        result = 0
        message = ""

        # limit
        try:
            # try to parse parameters
            limit = getSecondsFromTimeStr(pLimit)
            # err
            if limit is None:
                raise ValueError("%s is not a number or timestring" % (pLimit))
            else:
                limit = int(limit)
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLeft(pUserName, pOperation, limit)

        # ret
        return result, message

    # --------------- parameter execution methods for PlayTime --------------- #

    def processSetPlayTimeEnabled(self, pUserName, pPlayTimeEnabled):
        """Process PlayTime enabled flag"""
        # defaults
        isPlayTimeEnabled = None
        result = 0
        message = ""

        # check
        if str(pPlayTimeEnabled).lower() not in ("true", "false"):
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= ("please specify true or false")
        else:
            isPlayTimeEnabled = True if str(pPlayTimeEnabled).lower() == "true" else False

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeEnabled(pUserName, isPlayTimeEnabled)

        # ret
        return result, message

    def processSetPlayTimeLimitOverride(self, pUserName, pPlayTimeLimitOverride):
        """Process PlayTime override flag"""
        # defaults
        isPlayTimeLimitOverride = None
        result = 0
        message = ""

        # check
        if str(pPlayTimeLimitOverride).lower() not in ("true", "false"):
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= ("please specify true or false")
        else:
            isPlayTimeLimitOverride = True if str(pPlayTimeLimitOverride).lower() == "true" else False

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeLimitOverride(pUserName, isPlayTimeLimitOverride)

        # ret
        return result, message

    def processSetPlayTimeUnaccountedIntervalsEnabled(self, pUserName, pPlayTimeUnaccountedIntervalsEnabled):
        """Process PlayTime allowed during unaccounted intervals flag"""
        # defaults
        isPlayTimeUnaccountedIntervalsEnabled = None
        result = 0
        message = ""

        # check
        if str(pPlayTimeUnaccountedIntervalsEnabled).lower() not in ("true", "false"):
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= ("please specify true or false")
        else:
            isPlayTimeUnaccountedIntervalsEnabled = True if str(pPlayTimeUnaccountedIntervalsEnabled).lower() == "true" else False

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeUnaccountedIntervalsEnabled(pUserName, isPlayTimeUnaccountedIntervalsEnabled)

        # ret
        return result, message

    def processSetPlayTimeAllowedDays(self, pUserName, pPlayTimeDayList):
        """Process allowed days for PlayTime"""
        # defaults
        dayMap = []
        result = 0
        message = ""

        # day map
        try:
            # try to parse parameters
            dayMap = str(pPlayTimeDayList).split(";")
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeAllowedDays(pUserName, dayMap)

        # ret
        return result, message

    def processSetPlayTimeLimits(self, pUserName, pPlayTimeDayLimits):
        """Process time limits for allowed days for PlayTime"""
        # defaults
        dayLimits = []
        result = 0
        message = ""

        # day limists
        try:
            # allow empty limits too
            if str(pPlayTimeDayLimits) != "":
                # convert limits
                for rDl in str(pPlayTimeDayLimits).split(";"):
                    # limit
                    limit = getSecondsFromTimeStr(rDl)
                    # err
                    if limit is None:
                        raise ValueError("%s is not a number or timestring" % (rDl))
                    else:
                        limit = int(limit)
                    # add limit
                    dayLimits += [limit]
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeLimitsForDays(pUserName, dayLimits)

        # ret
        return result, message

    def processSetPlayTimeActivities(self, pUserName, pPlayTimeActivities):
        """Process PlayTime activities"""
        # defaults
        playTimeActivities = []
        result = 0
        message = ""

        # day limists
        try:
            # ## try to parse parameters ##
            if str(pPlayTimeActivities) != "":
                # split activities
                for rAct in str(pPlayTimeActivities).split(";"):
                    # try parsing the names
                    mask, description = splitConfigValueNameParam(rAct)
                    # raise any error in case we can not get parsing right
                    if mask is None:
                        # raise
                        raise ValueError("this does not compute")
                    # set up activity list
                    playTimeActivities.append([mask, description])
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeActivities(pUserName, playTimeActivities)

        # ret
        return result, message

    def processSetPlayTimeLeft(self, pUserName, pOperation, pLimit):
        """Process time left"""
        # defaults
        limit = 0
        result = 0
        message = ""

        # limit
        try:
            # try to parse parameters
            limit = getSecondsFromTimeStr(pLimit)
            # err
            if limit is None:
                raise ValueError("%s is not a number or timestring" % (pLimit))
            else:
                limit = int(limit)
        except Exception as ex:
            # fail
            result, message = msg.getTranslation(s="TK_MSG_PARSE_ERROR", e=True)
            message %= (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setPlayTimeLeft(pUserName, pOperation, limit)

        # ret
        return result, message
