QN990F macOS Picture Controller
================================

功能
----
把 Samsung QN990F 在 macOS 下做成更接近普通电脑显示器的行为：

1. 默认全局快捷键：
       Control + Command + P
   -> 发送 KEY_PICTURE_OFF，立即关闭画面。

2. 如果是本程序关掉的画面：
   -> 下一次键盘 / 鼠标 / 触控板输入自动发送 KEY_RETURN，恢复画面。

3. 可选：
   -> macOS 键鼠空闲 N 分钟后自动 KEY_PICTURE_OFF。

4. 默认尊重 macOS 的显示器保持唤醒 power assertions：
   -> 视频播放、演示软件如果明确告诉 macOS“保持显示器开启”，
      自动空闲关屏会暂停。


默认快捷键
----------
Windows 版：
    Control + Alt + P

macOS 版：
    Control + Command + P

按你的要求，Mac 上把 Alt 换成了 Command。


安装
----
1. 解压 ZIP。
2. 双击 INSTALL.command。

如果 macOS 因为文件来自互联网而阻止直接运行：
- Control-click INSTALL.command
- 选择 Open
- 再选择 Open

安装程序会询问：
- QN990F 的局域网 IP
- 自动关屏空闲时间（默认 10 分钟；输入 0 关闭自动空闲关屏）

然后自动完成：
- 在 ~/Library/Application Support/QN990FController 下安装独立运行环境
- 安装私有 Python 3.12
- 安装 samsungtvws 3.0.5
- 与电视配对
- 测试 KEY_PICTURE_OFF -> 2 秒后 KEY_RETURN
- 安装用户级 LaunchAgent，登录 macOS 后自动启动


不需要
------
- Homebrew
- 系统 pip
- Xcode / Command Line Tools
- sudo / root
- Accessibility 权限
- Input Monitoring 权限


为什么不需要 Accessibility / Input Monitoring
-----------------------------------------------
全局快捷键使用 macOS Carbon RegisterEventHotKey。

鼠标/键盘唤醒和空闲检测使用：
    CGEventSourceSecondsSinceLastEventType(..., kCGAnyInputEventType)

程序只读取“距离上一次输入过去了多少秒”。

它不会读取：
- 你具体按了哪个键
- 你输入的文字
- 鼠标坐标
- 具体的输入事件内容


正常使用
--------
Control + Command + P：
    立即 Picture Off。

Picture Off 后：
    动一下鼠标 / 触控板，或者按一个键 -> 自动恢复画面。

如果配置了自动空闲关屏：
    N 分钟无输入 -> KEY_PICTURE_OFF
    下一次输入 -> KEY_RETURN


视频 / 演示保护
---------------
默认情况下，当达到空闲时间时，程序会检查 macOS 的显示电源 assertion。

以下 assertion 会被当成“现在不要自动关显示器”：
- PreventUserIdleDisplaySleep
- NoDisplaySleepAssertion
- InternalPreventDisplaySleep

因此 Safari/Chrome 视频、播放器、演示软件等如果正确向 macOS 请求保持显示器唤醒，
不会因为你长时间没有碰键鼠而被本程序关屏。

如果某个应用播放视频时没有提交这种 assertion，它仍可能在达到空闲时间后被关屏。


重新配置
--------
安装以后双击：

~/Library/Application Support/QN990FController/Configure.command

可以修改：
- TV IP
- 自动空闲关屏时间
- 全局快捷键
- 是否尊重 macOS 的显示器保持唤醒 assertion

快捷键示例：
    Ctrl+Cmd+P
    Ctrl+Cmd+O
    Cmd+Shift+9
    Ctrl+Option+B

支持的修饰键名称：
    Ctrl / Control
    Cmd / Command
    Option / Opt / Alt
    Shift

最后一个按键支持 A-Z / 0-9。


文件位置
--------
~/Library/Application Support/QN990FController/config.json
    配置。

~/Library/Application Support/QN990FController/samsung-token.txt
    Samsung 配对 token，应当视为本地凭据。

~/Library/Application Support/QN990FController/controller.log
    控制器日志。

~/Library/Application Support/QN990FController/status.json
    当前/最近一次运行状态。

~/Library/LaunchAgents/local.qn990f.picture-controller.plist
    登录启动项。


重要限制
--------
1. KEY_PICTURE_OFF 是 Samsung TV 的遥控命令，不是 macOS 的 display sleep。
   设计目标就是让 HDMI 显示器仍然保持逻辑连接，避免正常电视关机/开机导致的
   HDMI 重新枚举、窗口重排等问题。

2. Samsung 固件可能静默忽略某些 KEY_*。
   所以安装程序会强制做一次视觉测试：
       Picture Off -> 等约 2 秒 -> KEY_RETURN
   只有你确认实际成功以后才启用登录自动启动。

3. 默认唤醒键是 KEY_RETURN。
   如果你的 QN990F 可以 Picture Off，但 KEY_RETURN 不能恢复，
   可把 config.json 中：
       "wake_key": "KEY_RETURN"
   改成：
       "wake_key": "KEY_UP"
   然后运行 Configure.command 重新启动。

4. 自动键鼠唤醒只针对“本控制器认为自己关掉了画面”的情况。
   如果你通过电视菜单/Bixby/其它设备关屏，本控制器不一定知道当前状态。

5. 建议在路由器中给 QN990F 做 DHCP Reservation，固定 IP。

6. 建议让 macOS 自己的显示器自动关闭时间比本控制器的时间更长，
   或在这个桌面使用场景中关闭 macOS 自己的自动 display sleep。
   否则 macOS 可能先让 HDMI 链路进入休眠。


手动测试
--------
Picture Off 约 2 秒再恢复：

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --test

只关屏：

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --off

只发送唤醒：

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --wake

查看当前键鼠空闲秒数：

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --idle


卸载
----
双击：

~/Library/Application Support/QN990FController/Uninstall.command

会删除：
- 控制器
- 私有 uv/Python 环境
- token / 配置 / 日志
- LaunchAgent

不会修改电视本身。
