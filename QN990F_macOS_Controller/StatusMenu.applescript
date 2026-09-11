use AppleScript version "2.4"
use framework "AppKit"
use scripting additions

property statusItem : missing value
property controllerLine : missing value
property volumeLine : missing value
property volumeSlider : missing value
property volumeValueLabel : missing value
property volumeNotice : missing value
property volumeTimer : missing value
property volumeSession : ""
property pendingVolumeID : ""
property pendingVolumeTime : 0
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
    set volumeView to current application's NSView's alloc()'s initWithFrame:{{0, 0}, {290, 65}}
    set volumeValueLabel to current application's NSTextField's labelWithString:"TV volume: loading"
    volumeValueLabel's setFrame:{{16, 39}, {260, 20}}
    volumeView's addSubview:volumeValueLabel
    set volumeSlider to current application's NSSlider's alloc()'s initWithFrame:{{16, 7}, {258, 28}}
    volumeSlider's setMinValue:0
    volumeSlider's setMaxValue:100
    volumeSlider's setTarget:me
    volumeSlider's setAction:"volumeChanged:"
    volumeSlider's setContinuous:true
    -- NSEventMask: left drag (1 << 6), left up (1 << 2), key down (1 << 10).
    -- These macro constants are not exported through the AppleScript bridge.
    volumeSlider's sendActionOn:1092
    volumeSlider's setAccessibilityLabel:"TV volume"
    volumeSlider's setEnabled:false
    volumeView's addSubview:volumeSlider
    set sliderItem to current application's NSMenuItem's alloc()'s init()
    sliderItem's setView:volumeView
    statusMenu's addItem:sliderItem
    set volumeNotice to current application's NSMenuItem's alloc()'s initWithTitle:"" action:(missing value) keyEquivalent:""
    volumeNotice's setEnabled:false
    statusMenu's addItem:volumeNotice
    statusMenu's addItem:(current application's NSMenuItem's separatorItem())
    statusMenu's addItem:(my actionItem("Repair Input Monitoring…", "repairInputMonitoring:"))
    statusMenu's addItem:(my actionItem("Bind Current TV Audio Output…", "bindAudioOutput:"))
    statusMenu's addItem:(my actionItem("Reauthorize SmartThings…", "reauthorizeSmartThings:"))
    statusMenu's addItem:(my actionItem("Restart Controller", "restartController:"))
    statusMenu's addItem:(my actionItem("Open Controller Log", "openLog:"))
    statusMenu's addItem:(current application's NSMenuItem's separatorItem())
    statusMenu's addItem:(my actionItem("Quit Controller", "quitController:"))
    statusItem's setMenu:statusMenu
    statusMenu's setDelegate:me
    set volumeTimer to current application's NSTimer's timerWithTimeInterval:1 target:me selector:"refreshVolumeTimer:" userInfo:(missing value) repeats:true
    current application's NSRunLoop's mainRunLoop()'s addTimer:volumeTimer forMode:(current application's NSRunLoopCommonModes)
    my refreshStatus()
end run

on readJSON(fileName)
    set jsonData to current application's NSData's dataWithContentsOfFile:(appDir & fileName)
    if jsonData is missing value then return missing value
    return current application's NSJSONSerialization's JSONObjectWithData:jsonData options:0 |error|:(missing value)
end readJSON

on refreshVolume()
    -- Do not move the thumb while the user is dragging it.
    if (current application's NSEvent's pressedMouseButtons()) is not 0 then return
    set info to my readJSON("volume-status.json")
    set controllerInfo to my readJSON("status.json")
    set isAvailable to false
    if info is not missing value and controllerInfo is not missing value then
        set isAvailable to ((info's objectForKey:"available") as boolean) and ((controllerInfo's objectForKey:"running") as boolean)
        set isAvailable to isAvailable and (((info's objectForKey:"pid") as integer) is ((controllerInfo's objectForKey:"pid") as integer))
        set newSession to (info's objectForKey:"session") as text
        if newSession is not volumeSession then
            set pendingVolumeID to ""
            set volumeSession to newSession
        end if
    end if
    volumeSlider's setEnabled:isAvailable
    if info is missing value then
        volumeValueLabel's setStringValue:"TV volume: unavailable"
        volumeNotice's setTitle:"Start or update the controller to use the slider."
        return
    end if
    if pendingVolumeID is not "" then
        if ((info's objectForKey:"request_id") as text) is pendingVolumeID then
            set pendingVolumeID to ""
        else if (current application's NSDate's timeIntervalSinceReferenceDate()) - pendingVolumeTime < 60 and isAvailable then
            return
        else
            set pendingVolumeID to ""
            volumeNotice's setTitle:"No response; restart the controller and try again."
            return
        end if
    end if
    set level to info's objectForKey:"value"
    if level is not missing value and (level's isKindOfClass:(current application's NSNumber)) as boolean then
        volumeSlider's setIntegerValue:(level as integer)
        volumeValueLabel's setStringValue:("TV volume: " & (level as integer) & " / 100")
    else
        volumeValueLabel's setStringValue:"TV volume: unknown"
    end if
    if isAvailable then
        volumeNotice's setTitle:((info's objectForKey:"message") as text)
    else
        set notice to (info's objectForKey:"message") as text
        if notice is "" then set notice to "Controller is stopped."
        volumeNotice's setTitle:notice
    end if
end refreshVolume

on volumeChanged_(sender)
    set targetVolume to sender's integerValue() as integer
    volumeValueLabel's setStringValue:("TV volume: " & targetVolume & " / 100")
    if (current application's NSEvent's pressedMouseButtons()) is not 0 then return
    if volumeSession is "" then return
    set requestID to current application's NSUUID's UUID()'s UUIDString() as text
    set requestData to current application's NSMutableDictionary's alloc()'s init()
    requestData's setObject:volumeSession forKey:"session"
    requestData's setObject:requestID forKey:"id"
    requestData's setObject:targetVolume forKey:"value"
    requestData's setObject:(current application's NSDate's alloc()'s init()'s timeIntervalSince1970()) forKey:"created_at"
    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:requestData options:0 |error|:(missing value)
    if jsonData's writeToFile:(appDir & "volume-request.json") atomically:true then
        set pendingVolumeID to requestID
        set pendingVolumeTime to current application's NSDate's timeIntervalSinceReferenceDate()
        volumeNotice's setTitle:("Setting TV volume to " & targetVolume & "…")
    else
        volumeNotice's setTitle:"Could not submit the volume change."
    end if
end volumeChanged_

on refreshVolumeTimer_(sender)
    my refreshVolume()
end refreshVolumeTimer_

on menuWillOpen_(sender)
    my refreshStatus()
end menuWillOpen_

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
    my refreshVolume()
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
