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

        // Register Option+Shift+A hotkey
        var hotKeyRef: EventHotKeyRef?
        let modifiers: UInt32 = UInt32(optionKey | shiftKey)
        let keyCode: UInt32 = 0x00
        let hotKeyID = EventHotKeyID(signature: OSType(0x564F5821), id: 1)
        RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)

        var eventSpec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { (_, event, _) -> OSStatus in
            let toggle = Process()
            toggle.executableURL = URL(fileURLWithPath: "/bin/bash")
            toggle.arguments = ["-c", "if [ -f /tmp/vox-recording ]; then rm /tmp/vox-recording; else touch /tmp/vox-recording; fi"]
            try? toggle.run()
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
        // Ensure node/claude are in PATH
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

        // Monitor Python — if it dies, quit Vox
        DispatchQueue.global().async {
            python.waitUntilExit()
            NSLog("[vox] Python exited, shutting down")
            DispatchQueue.main.async {
                NSApp.terminate(nil)
            }
        }

        NSLog("[vox] Vox running — Option+Shift+A to talk")
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
