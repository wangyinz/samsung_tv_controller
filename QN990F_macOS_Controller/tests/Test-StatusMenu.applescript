use AppleScript version "2.4"
use framework "AppKit"
use scripting additions

-- Loads the real implementation, but never runs its startup handler or creates
-- a visible status item. No event is posted and no installed state is accessed.
property assertions : 0

on assertThat(condition, messageText)
    if not condition then error messageText
    set assertions to assertions + 1
end assertThat

on visibleItemCount(theMenu)
    set total to 0
    repeat with menuItem in theMenu's itemArray() as list
        if not (menuItem's isHidden() as boolean) then set total to total + 1
    end repeat
    return total
end visibleItemCount

on run argv
    set scriptPath to item 1 of argv as text
    set scriptFile to (POSIX file scriptPath) as alias
    set implementation to load script scriptFile
    script fakeStatusItem
        property button : missing value
        property attachedMenu : missing value
        on setMenu_(newMenu)
            set attachedMenu to newMenu
        end setMenu_
    end script
    set fakeStatusItem's button to current application's NSStatusBarButton's alloc()'s initWithFrame:{{0, 0}, {30, 24}}
    set implementation's statusItem to fakeStatusItem
    set implementation's appDir to (item 2 of argv) & "/"
    implementation's buildStatusMenu()
    my assertThat(fakeStatusItem's attachedMenu is missing value, "An attached menu would intercept button actions")
    set previousMask to fakeStatusItem's button's sendActionOn:20 as integer
    my assertThat(previousMask is 20, "Both left and right mouse-up events must be enabled; got " & previousMask)
    my assertThat((fakeStatusItem's button's action() as text) is "statusItemClicked:", "Button action must be connected")

    my assertThat(not (implementation's wantsFullMenu(2, 0)), "Left-click should open volume only")
    my assertThat(implementation's wantsFullMenu(4, 0), "Right-click should open the full menu")
    my assertThat(implementation's wantsFullMenu(2, 262144), "Control-click should open the full menu")
    my assertThat(implementation's wantsFullMenu(2, 393216), "Control plus Shift should still open the full menu")
    my assertThat(not (implementation's wantsFullMenu(2, 1048576)), "Command-click should not be mistaken for Control-click")
    my assertThat(implementation's wantsFullMenu(10, 0), "Keyboard activation must keep recovery actions accessible")
    my assertThat(implementation's wantsFullMenu(0, 0), "Non-mouse activation must keep recovery actions accessible")

    set theMenu to implementation's statusMenu
    set theSlider to implementation's volumeSlider
    set allCount to theMenu's numberOfItems() as integer
    my assertThat(allCount is 12, "All recovery actions and the slider should be retained")
    set implementation's pendingVolumeID to "pending-test-request"
    set implementation's volumeSession to "test-session"
    theSlider's setIntegerValue:37
    repeat 20 times
        implementation's prepareStatusMenu(false)
        my assertThat(my visibleItemCount(theMenu) is 2, "Compact view should contain only the slider and its notice")
        implementation's prepareStatusMenu(true)
        my assertThat(my visibleItemCount(theMenu) is allCount, "Full menu should restore every action and separator")
    end repeat
    my assertThat((implementation's volumeSlider's isEqual:theSlider) as boolean, "Both views must share the same slider")
    my assertThat((theSlider's integerValue() as integer) is 37, "Switching views must not change the target volume")
    my assertThat(implementation's pendingVolumeID is "pending-test-request", "Switching views must not clear pending requests")
    my assertThat(implementation's volumeSession is "test-session", "Switching views must not change controller session")

    -- Exercise the unchanged submission path, writing only inside the test directory.
    if (current application's NSEvent's pressedMouseButtons()) is not 0 then
        return "PASS: " & assertions & " native AppKit assertions. SKIP: submission while a mouse button is held."
    end if
    implementation's volumeChanged_(theSlider)
    set requestData to current application's NSData's dataWithContentsOfFile:((item 2 of argv) & "/volume-request.json")
    my assertThat(requestData is not missing value, "Slider should still submit its target")
    set requestInfo to current application's NSJSONSerialization's JSONObjectWithData:requestData options:0 |error|:(missing value)
    my assertThat(((requestInfo's objectForKey:"value") as integer) is 37, "Submitted target must match the slider")
    my assertThat(((requestInfo's objectForKey:"session") as text) is "test-session", "Submitted target must use the current session")
    return "PASS: " & assertions & " native AppKit assertions; no visible menu or real TV commands."
end run
