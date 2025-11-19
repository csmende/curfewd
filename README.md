# Timekpr-nExT

## Keep control of computer usage

Timekpr-nExT is a fresh, simple and easy-to-use screen-time management
application. It helps you optimize the time spent on the computer by your
children, subordinates, or even yourself.

The application is intended for parents or supervisors who want to limit or
shape computer usage in a way that suits their household or environment.

</br>

## Overview

Timekpr-nExT is designed to give you reliable control over computer usage. This
includes the ability to forcibly end user sessions and apply other restrictions
or limitations.

Please be responsible and inform your users that their activity is being timed
and that their session may end abruptly. Notifications can be configured to warn
them ahead of time, but ultimately it is up to the user to pay attention to
these warnings.

</br>

When setting up restrictions, supervisors should read the descriptions of all
options and take some time to explore the application before enforcing any
settings. Different combinations of options can create a very tailored
experience, but remember that Timekpr-nExT is a restrictive tool by design.

</br>

_**Note**: This README is updated along with the `beta` releases. If you do not
see a specific option in the `stable` series, please wait for its release or use
the `beta` version._

</br>

## Navigation guide

**This README** contains all the details you need, but it is quite large.
Below is a simple guide to help you find what you are looking for.

* About functionality:

  - A short description of the [applications](#applications) with a
    [typical use case](#typicalusecase).  
    *(This is likely the section most parents or supervisors will want to read.)*

  - For a deeper understanding, see the
    [detailed description of functionality](#detaileddescription):

    - Information about the user's side of the software:
      [client application](#clientapplication)

    - Information about the supervisor's tools:
      [administration application](#administrationapplication)

  - Recently added important features:

    - Alternative restriction types:
      [suspend / lock / shutdown](#restrictionlockouttypes)

    - The ability to limit specific applications or games:
      [PlayTime functionality](#playtimeconfiguration)

    - User-controlled notifications:
      [user configurable notifications](#userconfigurablenotifications)

    - Time periods where usage does not count toward limits:
      ["freeride" time periods](#freerideintervals)

    - Logging to system journal:
      [systemd journal](#logtojournald)

  - CLI (console) options and file-based configuration:
    [additional configuration possibilities](#additionalconfigpossibilities)


* **Installation guide**:

  - Installation and removal
    [instructions](#installation) for most popular Linux systems


* **Support the project**:

  - You can support development by **[donating](#support)**


* Translations:

  - Help translate Timekpr-nExT into your
    [language](#translate)


* Disclaimer, questions, suggestions, and reporting bugs:

  - The disclaimer is located
    [here](#disclaimer), and information about questions, suggestions,
    and bug reports is found [here](#bugs)

</br>

<a name="applications"></a>
## Applications overview

Timekpr-nExT provides two user-facing applications that control how the system
manages computer time and restrictions.

</br>

#### Timekpr-nExT Client indicator icon

This small application places an indicator icon in the system notification area.
It shows remaining time, limits, and notifications to the user.

![Timekpr-nExT icon](https://git.launchpad.net/timekpr-next/plain/resource/screenshots/timekpr-next-screenshot-icon-system-notification-area.png)

_An informational "padlock" icon appears in the system’s notification area. This
is what users automatically see when they log in._

</br>

#### Timekpr-nExT Client application

The client application opens when the user clicks the indicator icon and selects
**"Limits & configuration"**. It provides more detailed information as well as
useful configuration options.

![Timekpr-nExT Client application](https://git.launchpad.net/timekpr-next/plain/resource/screenshots/timekpr-next-screenshot-client.png)

_This application displays detailed time limits and allows users to adjust some
aspects of the functionality. It is intended for the end user._

</br>

#### Timekpr-nExT Administration application

This application is designed for supervisors or parents. It lets you configure
time limits for any user on the system and adjust technical settings for how
Timekpr-nExT behaves.

![Timekpr-nExT Administration application](https://git.launchpad.net/timekpr-next/plain/resource/screenshots/timekpr-next-screenshot-admin.png)

_The administration tool provides multiple configuration tabs so supervisors can
customize restrictions and user experience as needed._

</br>

#### Core of Timekpr-nExT

Behind the scenes, Timekpr-nExT includes a background service (often called a
"daemon"—not a demon!) that monitors the system and enforces all restrictions.

There is no screenshot for it, but rest assured: it is always running quietly in
the background, doing its job for you. ;)

</br>

<a name="detaileddescription"></a>
## Description of functionality

This section explains how Timekpr-nExT works behind the scenes, rather than
listing every configuration option in detail.

**Please note:** Timekpr-nExT relies heavily on tool-tips for explaining
individual options. Hover your mouse over any setting to see its description.
These hints are very helpful for understanding what each option does.

</br>

### Overall features

Timekpr-nExT aims to be as precise and user-friendly as possible, while still
providing reliable restrictions.

A key part of this is predictable and accurate time tracking. By default,
Timekpr-nExT checks and updates the user’s time usage every 3 seconds. This
ensures precise accounting and provides clear information about remaining time.
If users want more or fewer notifications, they can adjust this themselves.

Time is not counted when it should not be. For example, Timekpr-nExT avoids
counting time when the session is inactive. A session is considered inactive
when:

- another user is using the computer via user switching, or  
- the current user’s session is locked and/or the screensaver is active.

Time is also not counted when the computer is sleeping or shut down.

The rest depends on how the supervisor configures the system. Keep reading for a
complete overview of everything Timekpr-nExT can do.

</br>

<a name="administrationapplication"></a>
### Administration application

The administration application is where supervisors or parents configure time
limits, restrictions, and various technical settings for Timekpr-nExT.

To open the administration tool, you must either run it as a superuser or add
yourself to the `timekpr` system group for password-less access. Most people use
superuser mode because it works immediately without additional setup.

Running it as superuser is simple: open your application menu, dock, or launcher
and choose **"(SU) Timekpr-nExT Control Panel (superuser mode)"**.

If you have added yourself to the `timekpr` system group, you can instead launch
**"Timekpr-nExT Control Panel"** without entering a password.


_**Be aware**: this will NOT work unless you are actually in the `timekpr` group, which
may require you to log out and back in to work or a simple restart will take care
of that._

_**Note**: The "(SU)" prefix exists because older Gnome3 versions would truncate long
application names, making the two modes look identical._

</br>

To add yourself to the `timekpr` group, you can use your desktop environment’s
user management tool, or run `sudo gpasswd -a $USER timekpr`

Replace `$USER` with the correct administrator account if needed. Do not add
your child’s or subordinate’s username. After adding yourself, you must log out
and back in for the change to take effect.

</br>

_**Please note**: Some configuration options are unavailable in password-less
mode. These advanced options are rarely needed and should only be used in
special situations. Running in superuser mode provides full access to all
settings._

</br>

### User configuration

To configure each user’s limits and restrictions, you must first select them from
the user list. The list is taken directly from your operating system, and the
initial configuration is created automatically. After that, the list is stored
in the configuration directory, and **the configuration remains even if the OS
user is deleted**.

This means you can re-create a system user without losing their Timekpr-nExT
settings.

_**Please note:** When you open the administration application, **no user is
pre-selected**. This is intentional to ensure the supervisor chooses the correct
user and avoids accidental misconfiguration._

</br>

#### Tab "Info & today"

As the name suggests, this tab shows how much time the user has already spent
today and how much time they still have available.

Some information is shown only when the user is currently logged in. Live data
is more accurate and lets you monitor activity in real time. When the user is
not logged in, Timekpr-nExT shows the saved state instead. This saved
information is always available but is refreshed less frequently.

Time adjustment controls allow supervisors to reward a user with extra time or,
if needed, reduce their available time for the day.

Remember that adding time does **not** override allowed time periods or the
maximum daily allowance. These rules remain in place regardless of adjustments.

_**Pro tip**: Move your mouse over any item to read its tool-tip for additional
details._

</br>

A special value called **"Continuous time available"** shows how much time the
user can stay logged in **without interruption**, based on their current daily
allowance and allowed time periods. However, it never counts more than “today +
tomorrow” combined.

Continuous time can stretch past midnight if you configure the allowed periods
correctly. For example, if today’s time period ends at 24:00 and tomorrow’s
period starts at 00:00, usage can continue smoothly across midnight.

</br>

#### Tab "Limit configuration"

This tab lets you configure a user’s time restrictions. You can mix and match
options to create the daily routine you want.

Before diving into the full explanation, here are the two most common setup
patterns:

* Allow the full day (24:00:00), but restrict access to specific intervals, e.g.
  09:00–12:00, 13:00–16:00, and 19:00–21:00.

* Give a fixed amount of time per day (e.g. 08:00:00), and define the time range
  during which they can use those hours, e.g. 09:00–21:00.

_**Please note:** Time values follow the ISO 8601 standard: Monday is the first
day of the week and the system uses 24-hour time._

---

##### Week day limits

This section configures each day of the week. You can enable or disable computer
usage for any day and set different time limits for each one.

Days must be enabled or disabled individually, but time limits can be edited for
multiple days at once. Select several days (use Ctrl+A for all), choose whether
you’re editing hours or minutes, and press the “+” or “–” buttons.

You may also edit a single day’s limit by clicking the value in the table and
typing it manually. This allows entering seconds too (e.g. 7 → 07:00:00,
7:1:15 → 07:01:15).

By default, every day is enabled and users have unlimited time available.

---

##### Hour intervals

Hour intervals define when the user is allowed to use the computer. These can be
configured per day, or for several days at once by selecting multiple entries.

To create an interval, click the **Create** button and then define the start and
end times by clicking the interval in the table. You can modify the interval
later if needed.

Shortcuts also work here: entering `7` becomes 07:00, and `7:15` becomes 07:15.

</br>

<a name="freerideintervals"></a>
A special type of interval marked with **“∞”** (unaccounted time interval) means
that any time used in that period **does not count** toward the daily allowance.
This effectively becomes **free time**, even if the user has no regular time
left.

This is especially useful for scheduled activities like online classes or
homework sessions, where usage should not reduce personal computer time.

</br>

Intervals must **not** overlap in any way. However, intervals may touch
continuously (e.g. 07:00–13:00 and 13:00–14:30). Adjacent intervals might merge
automatically.

Keep in mind: if there is even a one-minute gap between intervals, the user will
face their configured restriction during the gap—for example, session
termination.

A design choice in Timekpr-nExT also ensures that a single hour cannot contain
more than one interval. A period may start or end inside an hour, but two
separate intervals cannot exist within the same hour (e.g. 07:15–09:15 and
09:45–14:30 are **not** allowed, but 07:15–09:00 and 09:45–14:30 **are**).

After setting intervals, click **Verify** so the application can check for
mistakes. If all is correct, you can apply the configuration. If not, conflicting
intervals will be highlighted.

By default, the entire day is available.

---

##### Weekly and monthly limits

This section lets you adjust weekly and monthly time allowances. Select a period,
choose a time unit (day, hour, or minute), and press the “+” or “–” buttons to
change the limits.

You may also edit a limit directly by clicking the value and typing it manually.
This allows editing seconds too (though it’s rarely necessary). Shortcuts work
as well: entering `7` creates `07:00:00:00` (days:hours:minutes:seconds), and
entering `6:10:5:11` results in `06:10:05:11`.

These limits work together with daily limits and hour intervals. The time the
user actually gets is **the smallest** of all limits combined. This means that
even if the daily limit has not been reached, the user may be restricted if they
have already used up their weekly or monthly allowance.

By default, the whole week and month are unrestricted. For most families, there’s
no need to adjust these values unless weekly or monthly control is desired.

---

</br>

<a name="playtimeconfiguration"></a>
#### PlayTime configuration

PlayTime is a screen time limiting feature for specific applications or
processes. In PlayTime terminology, these are called **activities**.

PlayTime expands the usual time tracking by adding control over how long certain
applications can run. Effectively, it works like a focused process monitor.

Users may run configured activities for the allowed amount of PlayTime. Once the
allowance is used up, those applications are terminated. Running them again ends
them immediately.

Keep in mind that PlayTime is generally treated as a **special time limit inside
the regular time limit**, so all normal rules apply—except when **override
mode** is enabled (explained later).

PlayTime was originally designed for games, but supervisors may monitor any
software, such as web browsers, chat apps, or even calculators.

_**Please note:** PlayTime time is still counted even during “∞” (unaccounted)
intervals—**unless override mode is enabled**._

---

##### PlayTime options

This section contains PlayTime controls, including the main on/off switch and
the PlayTime override mode.

PlayTime must be enabled **per user**. If it is not enabled for a user, it will
not work for them. You must also enable the
[PlayTime master switch](#playtimemasterswitch); without it, PlayTime will not
function system-wide.

By default, PlayTime is disabled.

</br>

Below is an overview of PlayTime behavior and modes.

The **standard mode** is restrictive. Certain activities can be used for a set
amount of time. Once the limit is reached, the application is closed. For
example, if Steam is configured and the PlayTime limit is one hour, Steam will
close after one hour and cannot be used again until the next day.

The **override mode** changes the concept entirely. In override mode, the usual
PlayTime limits are ignored. Time spent at the computer is counted **only when
PlayTime activities are running**. In other words, the rest of the time becomes
“free time.”

Using the Steam example:  
The user can freely use the computer until they start Steam. Then the PlayTime
clock starts. If they close Steam before the time is fully used, the remaining
time is preserved, and general usage becomes unrestricted according to normal
rules.

</br>

The option **“Allowed during ‘∞’ intervals”** controls whether PlayTime
activities can run during unaccounted ("∞") intervals.

- If the option is **disabled**, activities cannot run during unaccounted time
  intervals at all, even in override mode. Using Steam as an example, the user
  will be unable to launch it during “∞” time.
- If the option is **enabled**, activities may run during unaccounted intervals.
  In that case, PlayTime still counts usage normally.
- If override mode is enabled during an unaccounted interval, **everything is
  free**: the user can use the activity with no limits, and no time will be
  counted toward any allowance.

This option is useful when unaccounted intervals are used for activities like
online school. Supervisors may prevent certain apps from running during these
periods by disabling “Allowed during ‘∞’ intervals.”

Override mode is not immediately intuitive, but once understood, it can be very
useful for precise time accounting.

By default, the option is enabled.

---

##### PlayTime limits

PlayTime limits work similarly to standard time allowances. The interface is the
same, but these limits apply **only to PlayTime activities**.

If a day is disabled for PlayTime, the user cannot run any monitored activities;
they will close instantly.

_**Please note:** PlayTime limits do not apply when override mode is enabled._

---

##### PlayTime activities

This is the most important PlayTime section. Here you configure the activities
(processes) that should be monitored. This list is not a simple allowlist or
denylist—each activity requires a **process mask**.

Determining the correct mask may require using a system monitor tool. Any KDE or
GNOME “System Monitor” works well. You can also use a console or SSH.

Linux applications can be installed almost anywhere and often by the user
themselves—Steam, Lutris, Heroic, and many others install games in user-owned
directories. Applications also do not identify themselves as “games” or
“restricted programs,” so automatic detection is essentially impossible.

That is why the supervisor must choose which programs to monitor.

</br>

So here is a general guide on how to determine which processes or masks can be
used when configuring PlayTime activities.

If you are not familiar with the terminal, this may look intimidating at first.
However, you can use graphical system monitoring tools instead. KDE and GNOME
both provide “System Monitor” applications that work well for this purpose.  
Look for columns such as **Process Name**, **Command**, or **Command Line**—these
are your best helpers. You can always search online or ask your community how to
identify an executable name.

The process mask for a PlayTime activity is usually just the executable name, or
the full command line if the
["Enhanced activity monitor"](#playtimeenhancedactivitymonitor) is enabled
(case-sensitive!). A simple terminal command such as `top -c -d 1` is
often enough to find the process you need.
Games typically use the most resources while running, so they appear near the
top of the list.

Look at the COMMAND column. If something looks like the activity you want to
limit, take the executable name only—without its full path or any arguments.
Example: If you see `/opt/discord-canary/DiscordCanary --type=renderer ...`
then the correct mask is just `DiscordCanary`. This is the part after the last
slash and before the arguments.

Some games behave poorly when switching windows (Alt-Tab). In that case, you can
connect via SSH from another device and check the process list there.

You may also enter a description for each activity. This description is shown to
the user instead of the mask. If no description is provided, the mask itself is
shown.

</br>

_**Please note**: Process masks can use RegExp (regular expressions), not
glob patterns. This is an expert feature and must be used carefully._

Always verify your RegExp. Incorrect expressions may terminate the wrong
processes or fail to match anything at all.

If a RegExp is invalid, Timekpr-nExT will treat it as a literal string.
For example, `*wine*` is not really a valid and useful RegExp, `.*wine.*` is
correct. If written incorrectly, the system will literally search for a process
named `*wine*`, which almost certainly does not exist.

RegExp support does not allow the symbols [], and you should avoid using
^ and $. Internally, Timekpr-nExT automatically anchors patterns, so a mask
like `.*wine.*` becomes similar to `^.*wine.*$` (or `/.*wine.*$` when matching
full command lines in enhanced mode).

PlayTime also uses smart caching and retrieves process lists efficiently. Only
processes belonging to the monitored user are checked, counted, or terminated.

PlayTime logic is active only when at least one user has PlayTime enabled and at
least one activity is configured.

---

</br>

#### Tab "Additional options"

This section provides extra per-user configuration options.

- **Track inactive sessions**: When enabled, every user session is accounted for,
  even if it is locked or another user is using the computer. Use with caution.
- **Hide icon and notifications**: Hides the Timekpr-nExT client icon and almost
  all notifications. Only critical notifications are shown. If enabled for an
  unrestricted user, they may not even notice Timekpr-nExT is present.
- **Restriction & lockout type**: Determines what action is taken when a user’s
  time runs out. Be careful when changing from the default.

---

##### Restriction / lockout types

**Terminate sessions**  
Default and restrictive. User session is forcibly logged out immediately.

**Kill sessions**  
Similar to terminate sessions. Attempts a soft kill, but effect is largely the
same.

**Shutdown computer**  
Restrictive option. Shuts down the computer when user time expires. Use with
caution in multi-user environments.

**Suspend computer**  
Lockout option. Suspends the computer without terminating sessions. More suited
for self-control rather than restricting usage. If the computer is woken with no
time left, it remains suspended until unlocked.

**Suspend / wakeup computer**  
Lockout option. Similar to suspend, but computer wakes up automatically at the
next available time period for the day. Requires specifying allowed wakeup hour
interval. Dependent on BIOS/UEFI RTC wakeup support.

**Lock screen**  
Lockout option. Locks the screen when time expires. If unlocked without available
time, it locks again shortly. Suited for self-control rather than full restriction.

---

### Timekpr-nExT configuration

This tab allows supervisors to configure technical behaviors. Usually, default
values are optimal.

#### Control options

**Final notification**  
Forces one last notification when user time is almost up, even if the user has
disabled notifications.

**Termination time**  
Specifies seconds left before applying the selected restriction/lockout. Allows
users to request more time before enforcement.

**Countdown time**  
Specifies seconds left before starting a continuous countdown notification for
the final seconds.

**Poll interval**  
Defines the frequency (in seconds) at which Timekpr-nExT updates user sessions
and time values. This effectively sets the resolution of time accounting.

**Save time**  
Specifies how often (in seconds) Timekpr-nExT saves calculated time values to
disk. Minimizes potential loss during crashes.

**Log level**  
Controls verbosity of logs:  
- Level 1: minimal (saves space, minimal info)  
- Level 2: standard (sufficient info for troubleshooting)  
- Level 3: verbose, memory-intensive, mostly useful for developers  
Log files are handled by `logrotate` and compressed, so space is not usually an
issue.

---

#### Tracked sessions

Timekpr-nExT tracks the following session types by default:

- `x11` (Xorg)
- `wayland`
- `mir` (less common)

Do not modify these unless you understand Linux session types.

#### Excluded sessions

By default, the following session types are **not tracked**:

- `tty` (console sessions via Ctrl+Alt+F[1-7])
- `unspecified` (other unknown types)

Modify only if necessary and with understanding of the system.

#### Excluded users

Allows supervisors to exclude **system users** from time accounting, such as
login managers. Do **not** enter regular users here, as it may cause errors.

<a name="playtimemasterswitch"></a>
#### Additional options (PlayTime)

Several options in this section are related to **PlayTime** functionality.

---

##### PlayTime enabled
Controls the **master switch for PlayTime**.  
- Must be enabled for PlayTime to work globally.  
- If disabled, no user activities will be tracked, regardless of individual PlayTime settings.

---

<a name="playtimeenhancedactivitymonitor"></a>
##### Enhanced activity monitor
Controls whether PlayTime checks the **first 512 characters of the full process command line**, including arguments, against registered activity/process masks.  

- Without this option, only the executable path and name are checked.  
- Enables advanced RegExp matching for complex scenarios, e.g., interpreted languages like Python or Java.  
- Example: For Minecraft (a Java application), a process mask could be `.*java.*minecraft.*`.  
- **Note:** Changes only apply to newly started processes.

---

<a name="clientapplication"></a>
### Client application

The Timekpr-nExT client shows time metrics and limited configuration options for the user.  

- Users can view tooltips for detailed explanations of every value.  
- Appearance may vary depending on the desktop environment ([differences](#desktopenvironmentdifferences)).

---

#### Daily limits
Displays:  
- Daily time allowance  
- Time period restrictions  
- Time spent / time left  

Key points:  
- Icon color reflects remaining time (configurable by the user).  
- Periods marked with "∞" do not count towards the daily limit.  
- Unaccounted time intervals turn the icon gray while active.

---

#### Additional limits
Displays weekly and monthly time limits and usage so far.

---

#### PlayTime limits
Displays:  
- Time allowance and activity list for PlayTime  
- Type of PlayTime mode enabled  
- Active activity count and descriptions  

**Note:** Tab is visible only if supervisor enabled PlayTime for the user.

---

<a name="userconfigurablenotifications"></a>
#### Notifications
Users can adjust notifications for time left:  
- Set thresholds for standard and PlayTime notifications  
- Assign priority levels  

**Note:** PlayTime notifications appear only if enabled by supervisor. Tooltips provide detailed guidance.

---

#### Configuration
Allows users to tailor Timekpr-nExT behavior:  
- Show seconds in icon  
- Enable or disable limit change notifications  
- Configure sound notifications (requires `python3-espeak` or similar)  
- Adjust notification types and log level  

**Note:** There may be a [sound notification bug](#quirkssound).

---

</br>

<a name="typicalusecase"></a>
## Typical use case

Imagine Liane wants to limit computer time for Cartman because he spends too much time online talking to Kyle and Stan _(Kenny doesn’t count—he doesn’t have a computer)_.  
She also wants to reserve 2 hours on Monday (15:00–17:00) for mandatory anger management classes.

Liane wants a flexible schedule within strict time windows for weekdays and holidays. Using Timekpr-nExT, she can configure it as follows:

---

**Configuration steps in Timekpr-nExT Administration:**

1. Select username **"cartman"** and open the **Limit configuration** tab.

2. **Monday:**
   - Set daily time limit: **6 hours**
   - Add interval **7:30–15:00**
   - Add interval **15:00–17:00**, check the "**∞**" box (time not counted against limit)
   - Add interval **17:00–21:00**

3. **Tuesday–Friday:**
   - Set daily time limit: **6 hours**
   - Add interval **7:30–21:00**

4. **Holidays (Saturday & Sunday):**
   - Set daily time limit: **8 hours**
   - Add interval **9:00–22:30**

5. Click **Apply daily limits** to save restrictions.

---

**Resulting behavior:**

- Cartman has **6 hours of computer time** Monday–Friday (7:30–21:00).  
  - **15:00–17:00** is free for mandatory classes (doesn’t consume daily limit).

- Cartman has **8 hours of computer time** during holidays (9:00–22:30).  

- Cartman **cannot use the computer** outside the defined intervals or exceed his daily allowance.

---

> Typical setups are straightforward, but Timekpr-nExT supports more advanced configurations.  
> For further details, see [Detailed information](#detaileddescription).

---

</br>

<a name="installation"></a>
## Installation / removal

To start using Timekpr-nExT, you need to install it. There are two versions: **beta** (early access) and **stable** (production-ready).  

> Tip: Logging out and back in after installation is highly recommended.

**Availability:**

- Ubuntu and derivatives: via **PPA**  
- ArchLinux / Manjaro: via **AUR**  
- Debian: native packages (may be outdated)  
- Fedora: via **johanh's COPR repository**  
- openSUSE: native packages (from 2025)  
- Manual installation: Fedora 32+ and openSUSE Leap 15.2+  
- NixOS: via **nixos-unstable** (and future nixos-25.11)

Installation via terminal is generally faster than GUI, but both methods are supported.

**NOTE**: I am not creating packages for manual installation anymore!
The ones which were created are for historical purposes or older installations only!!!

</br>

#### Stable

| Distribution | Stable install | Stable remove |
| :--- | :--- | :--- |
| Ubuntu & co (via PPA) | ```sudo add-apt-repository ppa:mjasnik/ppa```</br>```sudo apt-get update```</br>```sudo apt-get install timekpr-next``` | ```sudo apt-get remove --purge timekpr-next``` |
| ArchLinux & Manjaro (via AUR) | ```yay -S timekpr-next``` | ```sudo pacman -Rdd timekpr-next``` |
| Fedora | [copr repo](https://copr.fedorainfracloud.org/coprs/johanh/timekpr-next/) _(preferred)_</br>**or**</br>[manual installation](https://drive.google.com/drive/folders/1iN1wcPctGhd_OISqzWZ5DigFMVvgSGq9) _(README and packages)_| [copr repo](https://copr.fedorainfracloud.org/coprs/johanh/timekpr-next/) _(preferred)_</br>**or**</br>[manual uninstallation](https://drive.google.com/drive/folders/1iN1wcPctGhd_OISqzWZ5DigFMVvgSGq9) _(README)_ |
| openSUSE | [manual installation](https://drive.google.com/drive/folders/1iN1wcPctGhd_OISqzWZ5DigFMVvgSGq9)</br>_(README and packages)_| [manual uninstallation](https://drive.google.com/drive/folders/1iN1wcPctGhd_OISqzWZ5DigFMVvgSGq9)</br>_(README)_ |
| NixOS | set [services.timekpr.enable](https://search.nixos.org/options?channel=unstable&show=services.timekpr.enable) to `true` | set [services.timekpr.enable](https://search.nixos.org/options?channel=unstable&show=services.timekpr.enable) to `false` |

---

#### Beta

| Distribution | Beta install | Beta remove |
| :-- | :--: | --: |
| Ubuntu & co (via PPA) | ```sudo add-apt-repository ppa:mjasnik/ppa```</br>```sudo apt-get update```</br>```sudo apt-get install timekpr-next-beta``` | ```sudo apt-get remove --purge timekpr-next-beta``` |
| ArchLinux & Manjaro (via AUR) | ```yay -S timekpr-next-git``` | ```sudo pacman -Rdd timekpr-next-git``` |
| Fedora and openSUSE | [manual installation](https://drive.google.com/drive/folders/1iN1wcPctGhd_OISqzWZ5DigFMVvgSGq9)</br>_(README and packages)_ | [manual uninstallation](https://drive.google.com/drive/folders/1iN1wcPctGhd_OISqzWZ5DigFMVvgSGq9)</br>_(README)_ |

> **Note:** For ArchLinux and Manjaro, choose your preferred AUR helper if different from the examples above.  
> Special thanks to **SanskritFritz** (ArchLinux community) for beta testing and maintaining packages.  
> Thanks to **johanh** (Fedora community) for providing the COPR repo.

---

#### Debian

Timekpr-nExT is now available as a native Debian package thanks to **Sim (smntov)** and **Anthony Fok (foka)**.  
Preferred method is same as Ubuntu:

| Stable install | Stable remove | Beta |
| :--- | :--- | :--- |
| ```sudo apt-get update```</br>```sudo apt-get install timekpr-next``` | ```sudo apt-get remove --purge timekpr-next``` | Not available |

> **Note:** Packages built for Debian "sid" (unstable) may work on other versions like "buster" if dependencies are satisfied.  
> Graphical installers like **KDE Discover** or **GNOME Software** can also be used.  
> Always use a package created specifically for Debian for best compatibility.

</br>

## Compatibility

Timekpr-nExT **was** developed to be compatible with most Desktop Environments. During development, around 20 VMs **were** used to test major
changes, but not all possible setups are tested in the wild. Currently, testing is mostly on demand, based on bug reports or
developer discretion. In other words, nothing gets proactively tested until there is a reason.

Tested Desktop Environments include: **KDE, GNOME, Cinnamon, MATE, XFCE**  
Tested distributions include: **Ubuntu, Manjaro/ArchLinux, Debian, Fedora, openSUSE**

> Please check the [nuances](#quirks) section for more detailed information.  
> If you encounter an issue, see [this](#bugs) and file a bug report.

---

</br>

<a name="desktopenvironmentdifferences"></a>
## Information Representation Differences

Desktop Environments handle visual representation differently. Below are some key differences:

### Icon Differences
- The system tray icon can appear differently across environments:
  - **GNOME3 / Unity / XFCE**: show the icon with a detailed label beside it.
  - **KDE / Deepin**: show only the icon; hovering reveals the time left.

### Extensions May Be Needed
- Some GNOME3-based distributions require an extension to display the icon.  
- Recommended extension: [AppIndicator Support](https://extensions.gnome.org/extension/615/appindicator-support/)

### Notification Timeouts / Severity
- Certain desktop environments may override Timekpr-nExT’s notification timeout and severity settings.  
- KDE Plasma respects custom settings the best. Other environments may enforce preconfigured values.

### Future of the Icon
- GNOME3 may eventually remove the status icon functionality. If this happens, a workaround will be investigated.

---

</br>

## Short and Very Technical Overview

> **Warning:** This section is highly technical and not necessary for regular users.

Timekpr-nExT relies heavily on **DBUS** for system interaction:

- Uses DBUS interfaces provided by the system.
- Exposes its own DBUS interfaces.
- Can integrate with any client application or control panel that supports DBUS.

This architecture allows Timekpr-nExT to be highly extensible for technical users or administrators seeking deeper integration.

### Interfaces Used

Timekpr-nExT relies on several system interfaces for its functionality:

1. **systemd login1 interface**  
   - Used for session management.  
   - Mandatory for Timekpr-nExT; without it, the software will not function.  
   - Documentation: [systemd-logind](https://www.freedesktop.org/wiki/Software/systemd/logind/)

2. **FreeDesktop screensaver interfaces**  
   - Used to detect idle time and inhibit screensavers.  
   - Documentation: [Idle Inhibition Spec](https://people.freedesktop.org/~hadess/idle-inhibition-spec/ch05.html)  

3. **FreeDesktop notifications interface**  
   - Used to display notifications about time left.  
   - Documentation: [Notification Spec](https://specifications.freedesktop.org/notification-spec/latest/ar01s09.html)  

4. **GNOME screensaver interface (fallback)**  
   - Used if FreeDesktop screensaver interfaces are unavailable.  
   - Documentation: [GNOME Screensaver](https://people.gnome.org/~mccann/gnome-screensaver/docs/gnome-screensaver.html)

> Timekpr-nExT connects to these DBUS objects once, caches them, and mainly calls them during execution for efficiency.

---

<a name="additionalconfigpossibilities"></a>
### Additional Configuration Possibilities

Timekpr-nExT can be configured through:

1. **Graphical interface (GUI)**
2. **Command-line interface (CLI)**
3. **Direct configuration file editing**

---

#### CLI (Command Line Interface) Mode

- The **Administration application** can be run in a terminal to manage users and configure limits, with functionality equivalent to the GUI.  
- CLI usage is restricted to **user-level configuration only**.  
- To explore CLI options, run `sudo timekpra --help`

**Security Note for CLI Usage**  
CLI usage of Timekpr-nExT follows the same security rules as the graphical interface:  

- You must be in the `timekpr` group,  
- or execute the CLI as `root`,  
- or use `sudo` to gain access.  

Changes made in CLI or GUI are applied **immediately**, in real-time.

---

### Configuration by Files

Timekpr-nExT can also read configuration from files directly, which are applied at **save intervals** (default every 30 seconds).  

**Important Notes:**  
- Direct file editing is **not recommended**; prefer GUI or CLI.  
- Configuration files have a **specific internal structure**. Improper edits can break functionality.  
- If files are corrupted or unrecognizable, Timekpr-nExT will attempt to **salvage valid options** and **recreate defaults** for damaged entries.

### Configuration Files (Handle with Care)

| Purpose | File Location |
| --: | :-- |
| Main configuration file | `/etc/timekpr/timekpr.conf` |
| User configuration files (per user) | `/var/lib/timekpr/config/timekpr.*.conf` |
| User control files (per user) | `/var/lib/timekpr/work/timekpr.*.conf` |
| Client configuration file | `$HOME/.config/timekpr/timekpr.conf` |

---

<a name="logfiles"></a>
### Log Files

Timekpr-nExT writes logs to track execution and performance. These files are generally **not meant for regular inspection** by users.  
They mainly serve as a record in case something goes wrong and can be helpful for debugging.

- Log files contain mostly **technical details**; nothing sensitive except usernames (which can be obfuscated if necessary).  
- From version `0.5.1`, log level changes take effect immediately—no restart required.  
- Log size can be reduced by lowering log level in the administration app, though level 2 (default) is preferred for troubleshooting.

</br>

**Log File Locations**

| Logging Area | File Location |
| -- | :-- |
| Daemon log | `/var/log/timekpr.log` |
| Administration application logs | `/tmp/timekpra.*.log` |
| Client application logs | `/tmp/timekprc.*.log` |

---

**Note:** Since version 0.5.9, log entries may also be written to standard OS logging facilities if the right conditions are met.  
Specifically, if the `python3-systemd` package is installed, logs are sent to `journald`; otherwise, they remain in the usual log file locations.

</br>

<a name="logtojournald"></a>
**Accessing Logs via System Journal**

To gather logs from `journalctl`, use the following commands:

| Logging Area | Description | Command (current boot) | Command (all time) |
| -- | -- | -- | :-- |
| Daemon log | main log file, needed for bug reports | `sudo journalctl -b -t timekprd > timekpr.log` | `sudo journalctl -t timekprd > timekpr.log` |
| Administration application logs | for issues involving admin app | `sudo journalctl -b -t timekpra > timekpra.log` | `sudo journalctl -t timekpra > timekpra.log` |
| Client application logs | for issues with user client app | `sudo journalctl -b -t timekprc > timekprc.log` | `sudo journalctl -t timekprc > timekprc.log` |

After gathering the log files, **compress them** (e.g., using `tar.gz` or `zip`) and attach them to bug reports for faster troubleshooting.

Without logs, I **cannot investigate issues** — there’s no way to magically see into your system without this information! In other
words - **my crystal ball is broken**!

---

### Quirks

Linux distributions vary widely in how they implement standards, so certain **quirks** may appear in Timekpr-nExT:

- Desktop Environments (DEs) may implement specifications partially or differently.  
- Features might behave differently depending on the DE or distribution version.  
- Quirks can affect **appearance, functionality, or notifications**.  

> **Note:** These quirks may change as distributions and DEs evolve. Timekpr-nExT aims to support as many versions as possible, so some issues may persist on older versions.

<a name="quirkssound"></a>
#### Sound "bell" notifications
- If a user has enabled **Use sound "bell" for notifications**, critical notifications might not appear on the desktop.  
- They are still registered in the notification system and can be reviewed there.

---

#### Blinking cursor / black screen after session termination
- Timekpr-nExT relies heavily on `login1` DBUS interfaces to terminate user sessions.  
- On some systems, this can result in a **black screen or blinking cursor**, giving the impression that the system has frozen.  
- This is often due to older installations or when the session does not switch to the login manager automatically.  
- Workaround: Timekpr-nExT tries to detect and switch to the login manager after session termination.  
- Using the **"kill sessions"** option in lockout/restriction types usually resolves the issue.

---

#### Idle time accounting while screen is locked
- Different Desktop Environments handle screen locking differently:  
  - **Gnome3-based DEs:** Screen must be locked and turned off for session to be considered inactive.  
  - **KDE, XFCE:** Locking the screen is sufficient.  
  - **Deepin:** Screen locking is incompatible due to deviations from FreeDesktop / Gnome standards.

---

#### Technical nuances
- These discrepancies can affect **time accounting** and session handling.  
- Often, these issues cannot be fixed by Timekpr-nExT alone and depend on the DE or distribution version.

##### Time not accounted while user is active
- Rapid lid open/close can make `org.freedesktop.login1` report the session as inactive.  
- Mitigation: Enable **inactive session tracking**, but results may vary.

##### Time not accounted when computer is locked
- Some DEs expose `org.freedesktop.ScreenSaver` incorrectly (`GetActive` always false or method not implemented).  
- Some DEs implement `IdleHint` or `LockedHint` differently or not at all.  
- If these interfaces are unreliable, idle time tracking may fail. Workarounds are limited.

##### Empty screen after logout
- Occasionally, logging out users via `org.freedesktop.login1.Manager` may result in a black screen if the DE does not handle session termination properly.  
- Using **"kill sessions"** generally resolves this.

---

> **Summary:** Timekpr-nExT implements numerous workarounds to support popular DEs, but inconsistencies in Linux distributions can affect functionality. Many quirks are DE-specific and may require configuration adjustments or distribution updates.

</br>

<a name="support"></a>
## How You Can Help

A lot of effort went into developing Timekpr-nExT. If you appreciate the work and would like to contribute, you can do so via **PayPal** or **Bitcoin**:

- **PayPal (no account required):** [https://tinyurl.com/yc9x85v2](https://tinyurl.com/yc9x85v2)  
- **Bitcoin address:** `bc1q57wapz6lxfyxex725x3gs7nntm3tazw2n96nk3`

You can also help by **translating Timekpr-nExT** into your language: [https://translations.launchpad.net/timekpr-next](https://translations.launchpad.net/timekpr-next)

---

</br>

<a name="disclaimer"></a>
## Disclaimer

Timekpr-nExT originated from the need to improve **timekpr-revived**, focusing on precise time accounting and proper session termination. Initially, it was developed for personal use (to monitor my kid’s computer usage), but that need no longer exists.  

Key points:  

- **Proactive feature development is limited:** Expect feature additions mainly from user suggestions or requests.  
- **Testing on new distributions is limited:** Timekpr-nExT may not be actively tested on the latest OS versions unless bugs are reported.  
- **Configurability:** Timekpr-nExT is flexible; simple needs can be met without complexity, but advanced configurations are possible.

If the application meets your needs, consider showing support [here](#support). Otherwise, suggest features or improvements—you may see them implemented in future updates.

---

</br>

<a name="bugs"></a>
## Suggestions and Bug Reporting

- **Register suggestions and bugs:** [https://bugs.launchpad.net/timekpr-next](https://bugs.launchpad.net/timekpr-next)  
- **Ask questions:** [https://answers.launchpad.net/timekpr-next](https://answers.launchpad.net/timekpr-next)  
- **Prefix beta bugs:** Include `BETA` in the bug title if the issue was found in a beta version.  

You can also email suggestions/bugs to `edzis"replace this with at symbol"inbox"place a dot here"lv`

**REMEMBER: bug reporting without [log files](#logfiles) is useless!  
Always provide log files to bug reports and questions section, if the issue you are having is related to unexpected behaviour!**

</br>

**Bug report tips:**  

1. Describe the issue precisely and include steps to reproduce it.  
2. Mention your OS distribution, version, and Desktop Environment.  
3. Be ready to provide configuration and log files (no sensitive data except usernames).  

Submitting bugs through Launchpad is preferred, but email reports are also accepted, all this may be addressed as time permits.

---

Thanks for choosing Timekpr-nExT!
