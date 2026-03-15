import Cocoa
import Carbon

class VoxHelper: NSObject, NSApplicationDelegate {
    var screenshotTimer: Timer?
    var pythonProcess: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let home = NSHomeDirectory()
        let venvPython = "\(home)/kokoro-env/bin/python3.10"
        let resourcePath = Bundle.main.resourcePath ?? "."
        let appScript = "\(resourcePath)/app.py"
        let installScript = "\(resourcePath)/install.sh"

        // First-time setup: check for venv AND server script
        let serverScript = "\(home)/bin/kokoro-server.py"
        if !FileManager.default.fileExists(atPath: venvPython) || !FileManager.default.fileExists(atPath: serverScript) {
            let alert = NSAlert()
            alert.messageText = "Welcome to Vox!"
            alert.informativeText = "Vox needs to install a few things first:\n\n• Kokoro TTS (local voice)\n• Whisper STT (local speech recognition)\n• Python dependencies\n\nThis takes about 5 minutes.\n\nRequirements:\n• Python 3.10 (brew install python@3.10)\n• Claude Code (logged in)\n• tmux (brew install tmux)"
            alert.addButton(withTitle: "Install")
            alert.addButton(withTitle: "Quit")
            if alert.runModal() == .alertSecondButtonReturn {
                NSApp.terminate(nil)
                return
            }

            let installer = Process()
            installer.executableURL = URL(fileURLWithPath: "/bin/bash")
            installer.arguments = [installScript]
            var ienv = ProcessInfo.processInfo.environment
            let nvmDir = "\(home)/.nvm/versions/node"
            if FileManager.default.fileExists(atPath: nvmDir) {
                if let dirs = try? FileManager.default.contentsOfDirectory(atPath: nvmDir) {
                    for d in dirs {
                        ienv["PATH"] = "\(nvmDir)/\(d)/bin:" + (ienv["PATH"] ?? "")
                    }
                }
            }
            ienv["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (ienv["PATH"] ?? "")
            installer.environment = ienv
            try? installer.run()
            installer.waitUntilExit()

            if !FileManager.default.fileExists(atPath: venvPython) {
                let errAlert = NSAlert()
                errAlert.messageText = "Setup Failed"
                errAlert.informativeText = "Could not install dependencies.\n\nMake sure you have:\n• Python 3.10: brew install python@3.10\n• tmux: brew install tmux\n• Claude Code logged in\n\nThen reopen Vox."
                errAlert.runModal()
                NSApp.terminate(nil)
                return
            }
        }

        // Start kokoro-server if not running
        if !FileManager.default.fileExists(atPath: "/tmp/kokoro-tts.sock") {
            if FileManager.default.fileExists(atPath: serverScript) {
                NSLog("[vox] Starting kokoro-server...")
                let server = Process()
                server.executableURL = URL(fileURLWithPath: venvPython)
                server.arguments = [serverScript]
                var senv = ProcessInfo.processInfo.environment
                senv["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
                server.environment = senv
                try? server.run()
            }
        }

        let modifiers: UInt32 = UInt32(optionKey | shiftKey)

        // Register Option+Shift+A — toggle recording (key code 0x00 = 'a')
        var talkKeyRef: EventHotKeyRef?
        let talkKeyID = EventHotKeyID(signature: OSType(0x564F5821), id: 1)
        RegisterEventHotKey(0x00, modifiers, talkKeyID, GetApplicationEventTarget(), 0, &talkKeyRef)

        // Register Option+Shift+S — stop speech (key code 0x01 = 's')
        var stopKeyRef: EventHotKeyRef?
        let stopKeyID = EventHotKeyID(signature: OSType(0x564F5822), id: 2)
        RegisterEventHotKey(0x01, modifiers, stopKeyID, GetApplicationEventTarget(), 0, &stopKeyRef)

        // Single event handler that dispatches based on hotkey ID
        var eventSpec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { (_, event, _) -> OSStatus in
            var hotkeyID = EventHotKeyID()
            GetEventParameter(event!, EventParamName(kEventParamDirectObject),
                              EventParamType(typeEventHotKeyID), nil,
                              MemoryLayout<EventHotKeyID>.size, nil, &hotkeyID)

            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/bin/bash")

            if hotkeyID.id == 1 {
                // Toggle recording
                task.arguments = ["-c", "if [ -f /tmp/vox-recording ]; then rm /tmp/vox-recording; else touch /tmp/vox-recording; fi"]
            } else if hotkeyID.id == 2 {
                // Stop speech
                task.arguments = ["-c", """
                    python3 -c "
                    import socket, json
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect('/tmp/kokoro-tts.sock')
                    s.sendall(json.dumps({'cmd':'stop'}).encode())
                    s.close()
                    " 2>/dev/null
                    """]
            }

            try? task.run()
            return noErr
        }, 1, &eventSpec, nil, nil)

        // Watch for screenshot requests
        screenshotTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
            if FileManager.default.fileExists(atPath: "/tmp/vox-screenshot-request") {
                try? FileManager.default.removeItem(atPath: "/tmp/vox-screenshot-request")
                self.takeScreenshot()
            }
        }
        RunLoop.current.add(screenshotTimer!, forMode: .common)

        // Launch Python menu bar app
        let python = Process()
        python.executableURL = URL(fileURLWithPath: venvPython)
        python.arguments = [appScript]
        var env = ProcessInfo.processInfo.environment
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["VOX_NO_HOOK"] = "1"
        let extraPaths = [
            "\(home)/.nvm/versions/node",
            "/usr/local/bin",
            "/opt/homebrew/bin"
        ]
        var nodePaths: [String] = []
        let fm = FileManager.default
        for base in extraPaths {
            if base.contains(".nvm"), fm.fileExists(atPath: base) {
                if let dirs = try? fm.contentsOfDirectory(atPath: base) {
                    for d in dirs {
                        nodePaths.append("\(base)/\(d)/bin")
                    }
                }
            } else {
                nodePaths.append(base)
            }
        }
        let currentPath = env["PATH"] ?? "/usr/bin:/bin"
        env["PATH"] = nodePaths.joined(separator: ":") + ":" + currentPath
        python.environment = env
        python.standardOutput = FileHandle(forWritingAtPath: "/tmp/vox-debug.log") ?? FileHandle.nullDevice
        python.standardError = python.standardOutput
        do {
            try python.run()
            self.pythonProcess = python
            NSLog("[vox] Python app launched")
        } catch {
            NSLog("[vox] Failed to launch Python: \(error)")
        }

        DispatchQueue.global().async {
            python.waitUntilExit()
            NSLog("[vox] Python exited, shutting down")
            DispatchQueue.main.async {
                NSApp.terminate(nil)
            }
        }

        NSLog("[vox] Vox running — Opt+Shift+A to talk, Opt+Shift+S to stop speech")
    }

    func takeScreenshot() {
        let displayID = CGMainDisplayID()
        guard let image = CGDisplayCreateImage(displayID) else {
            NSLog("[vox] Screenshot failed — need Screen Recording permission")
            FileManager.default.createFile(atPath: "/tmp/vox-screenshot-done", contents: nil)
            return
        }
        let url = URL(fileURLWithPath: "/tmp/vox-screen.png")
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
            FileManager.default.createFile(atPath: "/tmp/vox-screenshot-done", contents: nil)
            return
        }
        CGImageDestinationAddImage(dest, image, nil)
        CGImageDestinationFinalize(dest)
        FileManager.default.createFile(atPath: "/tmp/vox-screenshot-done", contents: nil)
        NSLog("[vox] Screenshot captured")
    }

    func applicationWillTerminate(_ notification: Notification) {
        pythonProcess?.terminate()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = VoxHelper()
app.delegate = delegate
app.run()
