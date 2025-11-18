"""
Created on Aug 28, 2018

@author: mjasnik
"""

# imports
from datetime import datetime
import os

# timekpr imports
from timekpr.common.constants import constants as cons

# default logging
_LOG_LEVEL = cons.TK_LOG_LEVEL_INFO
# logging to file
_LOG_FILE = None
_LOG_NAME = "default"
_LOGGER = None
_LOG_FACILITY = cons.TK_LOG_FACILITY_EFF
# logging buffer (before we know where to put log file)
_LOG_PEND_EVT_CNT = 0
_LOG_PEND_FLUSH_TIM = datetime.now()
_LOG_BUFFER = []

# conditional imports
if _LOG_FACILITY > cons.TK_LOG_FACILITY_TKL:
    try:
        # std logging
        import logging
        # journald
        if _LOG_FACILITY > cons.TK_LOG_FACILITY_PYL:
            from systemd.journal import JournalHandler
    except:
        _LOG_FACILITY = cons.TK_LOG_FACILITY_TKL


# log names
def _getLogFileName(pWho, pUserName):
    """Get log file"""
    # log file
    logFile = (cons.TK_LOG_FILE_CLIENT if pWho == cons.TK_LOG_OWNER_CLIENT else (cons.TK_LOG_FILE_ADMIN if pWho == cons.TK_LOG_OWNER_ADMIN else (cons.TK_LOG_FILE_ADMIN_SU if pWho == cons.TK_LOG_OWNER_ADMIN_SU else cons.TK_LOG_FILE)))
    # replace user in log file
    return(logFile.replace(cons.TK_LOG_USER, pUserName, 1))


def setLogging(pLogLevel, pLogDir, pWho, pUserName):
    """Set up logging (this function expects 4 params: log level, log directory, log owner and username"""
    global _LOG_FACILITY, _LOG_FILE, _LOG_NAME, _LOGGER, _IS_JOURNALD_AVAILABLE
    # set up file
    _LOG_FILE = os.path.join(pLogDir, _getLogFileName(pWho, pUserName))
    # set up logging name
    _LOG_NAME = ("timekprc" if pWho == cons.TK_LOG_OWNER_CLIENT else ("timekpra" if pWho == cons.TK_LOG_OWNER_ADMIN else ("timekpra-su" if pWho == cons.TK_LOG_OWNER_ADMIN_SU else "timekprd")))

    # logger used
    if _LOG_FACILITY > cons.TK_LOG_FACILITY_TKL:
        # add python logger
        _LOGGER = logging.getLogger(_LOG_NAME)
        _LOGGER.setLevel(pLogLevel)

        # journald
        if _LOG_FACILITY > cons.TK_LOG_FACILITY_PYL:
            # set up
            lHandler = JournalHandler(SYSLOG_IDENTIFIER=_LOG_NAME)
        else:
            # set up file handler
            lHandler = logging.FileHandler(_LOG_FILE)

        # finish handler
        lHandler.setLevel(cons.TK_LOG_LEVEL_DEBUG)
        # set up logging format
        mFmt = logging.Formatter("%(message)s")
        lHandler.setFormatter(mFmt)
        # add journald handler
        _LOGGER.addHandler(lHandler)

    # set up level
    setLogLevel(pLogLevel)


def setLogLevel(pUsrLvl):
    """Set up log level"""
    global _LOG_LEVEL, _LOGGER

    # none = def
    if pUsrLvl == cons.TK_USR_LOG_LEVEL_NONE:
        # set
        _LOG_LEVEL = cons.TK_LOG_LEVEL_INFO
    elif pUsrLvl == cons.TK_USR_LOG_LEVEL_INFO:
        # set
        _LOG_LEVEL = cons.TK_LOG_LEVEL_INFO
    elif pUsrLvl == cons.TK_USR_LOG_LEVEL_DEBUG:
        # set
        _LOG_LEVEL = cons.TK_LOG_LEVEL_DEBUG
    elif pUsrLvl == cons.TK_USR_LOG_LEVEL_EXTRA_DEBUG:
        # set
        _LOG_LEVEL = cons.TK_LOG_LEVEL_EXTRA_DEBUG

    # logging facility is not default
    if _LOG_FACILITY > cons.TK_LOG_FACILITY_TKL:
        # for logger
        _LOGGER.setLevel(_LOG_LEVEL)


def isDebugEnabled(pDebugLevel=cons.TK_LOG_LEVEL_DEBUG):
    """Is debug enabled"""
    global _LOG_LEVEL
    # check whether debug is enabled
    return pDebugLevel >= _LOG_LEVEL


def log(pLvl, pText, pForceFlush=False):
    """Print to console"""
    global _LOG_LEVEL, _LOGGER, _LOG_FACILITY, _LOG_PEND_EVT_CNT
    # check debug level and output
    if pLvl >= _LOG_LEVEL:
        # log while logging itself is not intitialized
        if _LOG_FACILITY < cons.TK_LOG_FACILITY_SDL or (_LOG_FACILITY == cons.TK_LOG_FACILITY_SDL and _LOGGER is None):
            # format text
            logText = "%s: %s" % (datetime.now().strftime(cons.TK_LOG_DATETIME_FORMAT), pText)
            # add to pending event cnt
            _LOG_PEND_EVT_CNT += 1
            # prepare a line for file
            _LOG_BUFFER.append({"l": pLvl, "m": ("%s\n" if _LOG_FACILITY < cons.TK_LOG_FACILITY_PYL else "%s") % (logText)})
            # log only if enough calls are passed to log
            autoFlushLogFile(pForceFlush)
        else:
            # flush if there was somethig pending
            if _LOG_PEND_EVT_CNT > 0:
                # flush log file
                _flushLogFile()
            # format text
            logText = "%s: %s" % (datetime.now().strftime(cons.TK_LOG_DATETIME_FORMAT), pText)
            # non-custom logging
            if pLvl <= cons.TK_LOG_LEVEL_DEBUG:
                # debug
                _LOGGER.debug(logText)
            else:
                # debug
                _LOGGER.log(pLvl, logText)

        # in development mode, we spit out in console as well
        if cons.TK_DEV_ACTIVE:
            # print to console
            consoleOut(logText)

def consoleOut(*args):
    """Print everything passed to console"""
    # currently just output the stuff
    print(*args)


def autoFlushLogFile(pForceFlush=False):
    """This will flush the log file to file"""
    # iport globals
    global _LOG_PEND_FLUSH_TIM, _LOG_PEND_EVT_CNT

    # when the time has come, just flush the log file
    if (datetime.now() - _LOG_PEND_FLUSH_TIM).total_seconds() >= cons.TK_POLLTIME * 2 or _LOG_PEND_EVT_CNT >= cons.TK_LOG_AUTO_FLUSH_EVT_CNT or pForceFlush:
        # flush the log
        _flushLogFile()


def _flushLogFile():
    """This will flush the log file to file"""
    # iport globals
    global _LOG_FILE, _LOG_BUFFER, _LOGGER, _LOG_FACILITY, _LOG_PEND_EVT_CNT, _LOG_PEND_FLUSH_TIM

    # we can only flush if there is a file
    if _LOG_FILE is not None and len(_LOG_BUFFER) > 0:
        try:
            # timekpr logging
            if _LOG_FACILITY < cons.TK_LOG_FACILITY_PYL:
                # open log file
                with open(_LOG_FILE, "a") as logFile:
                    # loop
                    for rM in _LOG_BUFFER:
                        # write whole buffer to log file
                        logFile.write(rM["m"])
            # get back until we have set up logger
            elif _LOG_FACILITY > cons.TK_LOG_FACILITY_TKL and _LOGGER is None:
                return
            else:
                # loop
                for rM in _LOG_BUFFER:
                    # non-custom logging
                    if rM["l"] <= cons.TK_LOG_LEVEL_DEBUG:
                        # debug
                        _LOGGER.debug(rM["m"])
                    else:
                        # debug
                        _LOGGER.log(rM["l"], rM["m"])

            # reset
            _LOG_PEND_EVT_CNT = 0
            _LOG_PEND_FLUSH_TIM = datetime.now()
            _LOG_BUFFER.clear()
        except Exception as ex:
            # spit out to console
            consoleOut("ERROR: can not write to log due to:\n%s" % (ex))
