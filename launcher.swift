import Cocoa
import Carbon

class VoxHelper: NSObject, NSApplicationDelegate {
    var screenshotTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Register Option+Shift+A hotkey (no Accessibility permission needed)
        var hotKeyRef: EventHotKeyRef?
        let modifiers: UInt32 = UInt32(optionKey | shiftKey)
        let keyCode: UInt32 = 0x00  // 'a' key
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

        // Watch for screenshot requests from Python
        screenshotTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
            if FileManager.default.fileExists(atPath: "/tmp/vox-screenshot-request") {
                try? FileManager.default.removeItem(atPath: "/tmp/vox-screenshot-request")
                self.takeScreenshot()
            }
        }
        RunLoop.current.add(screenshotTimer!, forMode: .common)

        NSLog("[vox] Vox helper running — Option+Shift+A to talk")
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
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = VoxHelper()
app.delegate = delegate
app.run()
