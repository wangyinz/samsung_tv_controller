use AppleScript version "2.4"
use framework "AppKit"
use scripting additions

property statusItem : missing value
property controllerLine : missing value
property volumeLine : missing value
property appDir : ""

on run
    set appDir to POSIX path of (path to application support from user domain) & "QN990FController/"
    set statusItem to current application's NSStatusBar's systemStatusBar's statusItemWithLength:(current application's NSVariableStatusItemLength)
    statusItem's button's setTitle:"TV"

    set statusMenu to current application's NSMenu's alloc()'s initWithTitle:"Samsung TV Picture Controller"
    set controllerLine to current application's NSMenuItem's alloc()'s initWithTitle:"Controller: loading" action:(missing value) keyEquivalent:""
    controllerLine's setEnabled:false
    statusMenu's addItem:controllerLine
    set volumeLine to current application's NSMenuItem's alloc()'s initWithTitle:"Volume: loading" action:(missing value) keyEquivalent:""
    volumeLine's setEnabled:false
    statusMenu's addItem:volumeLine
    statusMenu's addItem:(current application's NSMenuItem's separatorItem())
    statusMenu's addItem:(my actionItem("Repair Input Monitoring…", "repairInputMonitoring:"))
    statusMenu's addItem:(my actionItem("Bind Current TV Audio Output…", "bindAudioOutput:"))
    statusMenu's addItem:(my actionItem("Reauthorize SmartThings…", "reauthorizeSmartThings:"))
    statusMenu's addItem:(my actionItem("Restart Controller", "restartController:"))
    statusMenu's addItem:(my actionItem("Open Controller Log", "openLog:"))
    statusMenu's addItem:(current application's NSMenuItem's separatorItem())
    statusMenu's addItem:(my actionItem("Quit Controller", "quitController:"))
    statusItem's setMenu:statusMenu
    my refreshStatus()
end run

on actionItem(itemTitle, selectorName)
    set menuItem to current application's NSMenuItem's alloc()'s initWithTitle:itemTitle action:selectorName keyEquivalent:""
    menuItem's setTarget:me
    return menuItem
end actionItem

on plistValue(filePath, keyName, fallbackValue)
    try
        return do shell script "/usr/bin/plutil -extract " & quoted form of keyName & " raw -o - " & quoted form of filePath
    on error
        return fallbackValue
    end try
end plistValue

on refreshStatus()
    set controllerState to my plistValue(appDir & "status.json", "state", "stopped")
    set volumeState to my plistValue(appDir & "health.json", "volume_control_state", "unknown")
    set volumeMessage to my plistValue(appDir & "health.json", "volume_control_message", "No volume status is available.")
    controllerLine's setTitle:("Controller: " & controllerState)
    volumeLine's setTitle:("Volume: " & volumeMessage)
    if controllerState is "authorization_required" or controllerState is "error" or controllerState is "stopped" then
        statusItem's button's setTitle:"TV×"
    else if volumeState is "input_permission_required" or volumeState is "binding_required" or volumeState is "error" then
        statusItem's button's setTitle:"TV!"
    else
        statusItem's button's setTitle:"TV"
    end if
end refreshStatus

on idle
    my refreshStatus()
    return 3
end idle

on repairInputMonitoring_(sender)
    do shell script "/usr/bin/open " & quoted form of (appDir & "RepairInputMonitoring.command")
end repairInputMonitoring_

on bindAudioOutput_(sender)
    do shell script "/usr/bin/open " & quoted form of (appDir & "BindAudioOutput.command")
end bindAudioOutput_

on reauthorizeSmartThings_(sender)
    do shell script "/usr/bin/open " & quoted form of (appDir & "Reauthorize.command")
end reauthorizeSmartThings_

on restartController_(sender)
    do shell script "/bin/launchctl kickstart -k gui/" & (do shell script "/usr/bin/id -u") & "/local.qn990f.picture-controller"
end restartController_

on openLog_(sender)
    do shell script "/usr/bin/open " & quoted form of (appDir & "controller.log")
end openLog_

on quitController_(sender)
    set userID to do shell script "/usr/bin/id -u"
    try
        do shell script "/bin/launchctl bootout gui/" & userID & "/local.qn990f.picture-controller"
    end try
    current application's NSApp's terminate:me
end quitController_
