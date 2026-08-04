"""
Created on Aug 28, 2018

@author: mjasnik
"""

# defaults
_START_TIME = None
_END_TIME = None
_RESULT = 0
_LOCK_FILE = None

# imports
from datetime import datetime
import os
import pwd
import inspect
import sys
import traceback
import re
import tempfile
import fcntl
try:
    import psutil
    _PSUTIL = True
except (ImportError, ValueError):
    _PSUTIL = False
    pass

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.log import log

# regular expression to match time formats (days or hours or minutes or seconds or seconds w/o suffix)
_TIMESTR_MATCH_PATTERN = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+))?$")
_DBUSNAME_REPLACE_PATTERN = re.compile(r'[^a-zA-Z0-9_]')


def whoami():
    """Return callers name from the call stack, the 0 is this function, prev is the one needed"""
    # result
    return inspect.stack()[1][3]


def getExceptionDetails():
    """Log exception details (can be used only when exception happens)"""
    exc_type, exc_value, exc_traceback = sys.exc_info()
    tb = traceback.extract_tb(exc_traceback)[-1]
    # result
    return "file: %s, function: %s, line: %s, error: %s" % (tb.filename, tb.name, tb.lineno, exc_value)


def getNormalizedUserNames(pUID=None, pUser=None):
    """Get usernames and/or normalize them"""
    user = pUser
    userName = None
    userNameFull = ""

    try:
        # if we need to get one
        if pUID is not None:
            # user
            user = pwd.getpwuid(pUID)
        # we have user
        if user is not None:
            # username
            userName = user.pw_name
            userNameFull = user.pw_gecos
        # workaround for distros that have one or more "," at the end of user full name
        userNameFull = userNameFull.rstrip(",")
        # if username is exactly the same as full name, no need to show it separately
        userNameFull = userNameFull if userNameFull != userName else ""
    except KeyError:
        pass

    # full username
    return userName, userNameFull


def getNormalizedDBUSName(pStr):
    """Get normalized username as dbus name (allowed chars only)"""
    global _DBUSNAME_REPLACE_PATTERN
    # full username
    return _DBUSNAME_REPLACE_PATTERN.sub("_", pStr)


def measureTimeElapsed(pStart=False, pStop=False, pResult=False):
    """Calculate the time difference in the simplest manner"""
    # init globals (per import)
    global _START_TIME
    global _END_TIME
    global _RESULT

    # set up start
    if pStart:
        _START_TIME = datetime.now()
    # set up end
    if pStop:
        # calc seconds and finish stuff
        _END_TIME = datetime.now()
        _RESULT = (_END_TIME - _START_TIME).total_seconds()
        _START_TIME = _END_TIME

    # return
    return _RESULT


def measureDBUSTimeElapsed(pStart=False, pStop=False, pPrintToConsole=False, pDbusIFName=""):
    """Calculate the time difference in the simplest manner"""
    # run
    result = measureTimeElapsed(pStart, pStop)
    # in case we measure dbus performance issues, just print them
    if pStop and result >= cons.TK_DBUS_ANSWER_TIME:
        # measurement logging
        if pPrintToConsole:
            # measurement logging
            log.consoleOut("WARNING: PERFORMANCE (DBUS) - acquiring \"%s\" took too long (%is)" % (pDbusIFName, result))
        else:
            # measurement logging
            log.log(cons.TK_LOG_LEVEL_WARN, "WARNING: PERFORMANCE (DBUS) - acquiring \"%s\" took too long (%is)" % (pDbusIFName, result))

    # return
    return result


def checkAndSetRunning(pAppName, pUserName=""):
    """Check whether application is already running"""
    # glo
    global _LOCK_FILE
    # def
    isAlreadyRunning = False
    # set up pidfile name
    pidFileName = "%s.%s" % ((pAppName if pUserName == "" else "%s.%s" % (pAppName, pUserName)), cons.TK_LOG_PID_EXT)

    # determine the location to put pid/lock file in
    for rDir in cons.TK_LOG_PID_DIRS:
        # if path is variable, let's get it
        if rDir.startswith("$"):
            # get location
            pidFile = os.getenv(rDir[1:])
        else:
            # def
            pidFile = rDir
        # set?
        if pidFile is None or pidFile == "":
            continue

        # check if we can write to it
        try:
            with tempfile.TemporaryFile(dir=pidFile):
                pass
        except Exception:
            continue

        # assemble the final
        pidFile = os.path.join(pidFile, pidFileName)

        # try to get the lock on the file
        try:
            # file
            _LOCK_FILE = open(pidFile, "w")
            # try lock
            fcntl.flock(_LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # write pid to it (not needed rly, but still)
            _LOCK_FILE.write(str(os.getpid()))
            # write
            _LOCK_FILE.flush()
            # msg
            log.log(cons.TK_LOG_LEVEL_WARN, "pid file written to %s" % (pidFile))
        except BlockingIOError:
            # we are running
            isAlreadyRunning = True
            # print this to console as well
            log.log(cons.TK_LOG_LEVEL_WARN, "Timekpr-nExT \"%s\" is already running for user \"%s\"" % (pAppName, pUserName if pUserName != "" else "root"))
            if pUserName != "":
                print("Timekpr-nExT \"%s\" is already running for user \"%s\"" % (pAppName, pUserName if pUserName != "" else "root"))

        # fin
        break

    # return whether we are running
    return isAlreadyRunning


def killLeftoverUserProcesses(pUserName, pTimekprConfig):
    """Kill leftover processes for user"""
    # if psutil is not available, do nothing
    global _PSUTIL
    if not _PSUTIL:
        return

    # determine which sessions we are going to kill (either graphical or tty)
    # this is somewhat interesting as for processes we cannot exactly tell whether it's graphical or not, but we check terminal sessions,
    # if terminal is not set, then it's assumed graphical or so
    killTty = False
    killGUI = False
    killedProcesses = 0
    otherProcesses = 0

    # build up killing session types
    sessinTypesForKill = [rSessionType for rSessionType in pTimekprConfig.getTimekprSessionsCtrl() if rSessionType not in pTimekprConfig.getTimekprSessionsExcl()]

    # check for graphical
    for sessionType in cons.TK_SESSION_TYPES_CTRL.split(";"):
        # check for kill
        if sessionType in sessinTypesForKill:
            killGUI = True
            break
    # check for graphical
    for sessionType in cons.TK_SESSION_TYPES_EXCL.split(";"):
        # check for kill
        if sessionType in sessinTypesForKill:
            killTty = True
            break

    # get all processes for this user
    for userProc in psutil.process_iter():
        # process info
        procInfo = userProc.as_dict(attrs=["pid", "ppid", "name", "username", "terminal"])
        # check for username and for processes that originates from init (the rest should be terminated along with the session)
        if procInfo["username"] == pUserName:
            # if originates from init
            if procInfo["ppid"] in (0, 1):
                # normalize terminal (only real terminals are considered terminals)
                terminal = procInfo["terminal"] if (procInfo["terminal"] is not None and "/dev/pts/" not in procInfo["terminal"]) else None
                # logging
                log.log(cons.TK_LOG_LEVEL_INFO, "INFO: got leftover process, pid: %s, ppid: %s, username: %s, name: %s, terminal: %s, effective terminal: %s" % (procInfo["pid"], procInfo["ppid"], procInfo["username"], procInfo["name"], procInfo["terminal"], terminal))
                # kill processes if they are terminal and terminals are tracked or they are not terminal processes
                if (terminal is not None and killTty) or (terminal is None and killGUI):
                    try:
                        # get process and kill it
                        userPrc = psutil.Process(procInfo["pid"])
                        # killing time
                        if cons.TK_DEV_ACTIVE:
                            log.log(cons.TK_LOG_LEVEL_WARN, "DEVELOPMENT ACTIVE, not killing my own processes, sorry...")
                        else:
                            # asking process to terminate
                            userPrc.terminate()
                    except psutil.Error as psErr:
                        log.log(cons.TK_LOG_LEVEL_ERR, "ERROR: killing %s failed (%s)" % (procInfo["pid"], str(psErr)))
                        pass
                    else:
                        # count killed processes
                        killedProcesses += 1
                else:
                    # do not kill terminal sessions if ones are not tracked
                    log.log(cons.TK_LOG_LEVEL_INFO, "INFO: NOT killing process %s as it's from sessions which are not being tracked" % (procInfo["pid"]))
            else:
                # count other processes
                otherProcesses += 1
    # log
    log.log(cons.TK_LOG_LEVEL_INFO, "INFO: %i session related processes were killed, %i other processes for user were not killed" % (killedProcesses, otherProcesses))


def findHourStartEndMinutes(pStr):
    """Separate name and desription in brackets"""
    # hour, start, end
    hour = None
    sMin = None
    eMin = None
    uacc = None

    # get len beforehand
    ln = len(pStr) if pStr is not None else 0

    # it makes sense to calc stuff only when there is a hour defined
    if ln > 0:
        # is hour unaccounted
        uacc = True if pStr[0] == "!" else False
        # in case of unlimited hour actual len is smaller
        ln = ln - 1 if uacc else ln
        # get hour (ex: 1 or 11)
        if 1 <= ln <= 2:
            # hour, start, end
            hour = pStr[1:] if uacc else pStr
            sMin = 0
            eMin = 60
        # get hours and minutes (ex: 1[1:1] or 11[11:22])
        elif 6 <= ln <= 9:
            # find minutes
            beg = 1 if uacc else 0
            st = pStr.find("[")
            sep = pStr.find("-")
            # failover to : (currently undocumented)
            sep = sep if not sep < 0 else pStr.find(":")
            en = pStr.find("]")
            # in case user config is broken, we cannot determine stuff
            if st < 0 or en < 0 or sep < 0 or not st < sep < en:
                # nothing
                pass
            else:
                # hour, start, end
                try:
                    # determine hour and minutes (and check for errors as well)
                    hour = int(pStr[beg:st])
                    sMin = int(pStr[st+1:sep])
                    eMin = int(pStr[sep+1:en])
                    # checks for errors (and raise one if there is an error)
                    hour = hour if 0 <= hour <= 23 else 1/0
                    sMin = sMin if 0 <= sMin <= 60 else 1/0
                    eMin = eMin if 0 <= eMin <= 60 else 1/0
                    eMin = eMin if sMin < eMin else 1/0
                except (ValueError, ZeroDivisionError):
                    # hour, start, end
                    hour = None
                    sMin = None
                    eMin = None
                    uacc = None

    # return
    return hour, sMin, eMin, uacc


def splitConfigValueNameParam(pStr):
    """Separate value and param in brackets"""
    # name and its value
    value = None
    param = None

    # nothing
    if len(pStr) < 2:
        # can not be a normal value
        pass
    else:
        try:
            # find description ("") is for backwards compatibility
            st = pStr.find("(\"")  # compatibility description start
            en = pStr.find("\")")  # compatibility description end
            ln = 1 if st < 0 else 2  # compatility case searches for 2 letters, new one 1
            # new style config
            st = pStr.find("[") if st < 0 else st  # new style config
            en = pStr.find("]") if en < 0 else en  # new style config
            st = en if st < 0 else st  # no description, we'll get just pattern
            # process and its description
            value = pStr[0:st if st > 0 else len(pStr)]
            param = "" if st < 0 else pStr[st+ln:en if en >= 0 else len(pStr)]
        except:
            # it doesn't matter which error occurs
            value = None
            param = None

    # return
    return value, param


def getSecondsFromTimeStr(pTimeStr):
    # glo
    global _TIMESTR_MATCH_PATTERN
    # def
    secs = None

    # no val
    if pTimeStr is not None:
        # rep
        pTimeStr = pTimeStr.replace(" ", "")
        # nothing?
        if not pTimeStr == "":
            # find matches
            match = _TIMESTR_MATCH_PATTERN.match(pTimeStr)
            # no match - time format is wrong
            if match:
                # extract matched values
                secs = ((int(match.group(1) or 0) * 86400)         # days
                    + (int(match.group(2) or 0) * 3600)            # hours
                    + (int(match.group(3) or 0) * 60)              # minutes
                    + int(match.group(4) or match.group(5) or 0))  # seconds

    # fin
    return secs


def getTimeStrFromSeconds(pSeconds):
    # for building our final string
    parts = []
    # secs
    seconds = abs(pSeconds)
    # get sign
    parts += ["-"] if seconds < 0 else []
    # secs
    seconds = abs(seconds)

    # calc stuff
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    # days
    parts += ["%sd" % (days)] if days else []
    # hrs
    parts += ["%sh" % (hours)] if hours else []
    # mins
    parts += ["%sm" % (minutes)] if minutes else []
    # seconds left
    parts += ["%ss" % (seconds)] if seconds or len(parts) == 0 else []

    # fin
    return "".join(parts)
