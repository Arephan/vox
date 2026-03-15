import Cocoa
import Carbon

class VoxApp: NSObject, NSApplicationDelegate {
    var pythonProcess: Process?
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        let resourcePath = Bundle.main.resourcePath ?? ""
        let home = NSHomeDirectory()
        let venvPython = "\(home)/kokoro-env/bin/python3.10"
        let appScript = "\(resourcePath)/app.py"
        let installScript = "\(resourcePath)/install.sh"
        let toggleScript = "\(resourcePath)/vox-toggle.sh"
        
        // Check if kokoro-env exists, if not run installer
        if !FileManager.default.fileExists(atPath: venvPython) {
            NSLog("[vox] First launch — running installer")
            let alert = NSAlert()
            alert.messageText = "Vox Setup"
            alert.informativeText = "Vox needs to install dependencies (Kokoro TTS, Whisper STT). This takes a few minutes.\n\nRequires: Python 3.10 (brew install python@3.10)\n\nClick Install to continue."
            alert.addButton(withTitle: "Install")
            alert.addButton(withTitle: "Quit")
            if alert.runModal() == .alertSecondButtonReturn {
                NSApp.terminate(nil)
                return
            }
            
            let installer = Process()
            installer.executableURL = URL(fileURLWithPath: "/bin/bash")
            installer.arguments = [installScript]
            installer.environment = ProcessInfo.processInfo.environment
            try? installer.run()
            installer.waitUntilExit()
            
            if !FileManager.default.fileExists(atPath: venvPython) {
                let errAlert = NSAlert()
                errAlert.messageText = "Install Failed"
                errAlert.informativeText = "Could not set up dependencies. Make sure Python 3.10 is installed:\n\nbrew install python@3.10\n\nThen relaunch Vox."
                errAlert.runModal()
                NSApp.terminate(nil)
                return
            }
        }
        
        // Start kokoro-server if not running
        if !FileManager.default.fileExists(atPath: "/tmp/kokoro-tts.sock") {
            NSLog("[vox] Starting kokoro-server")
            let server = Process()
            server.executableURL = URL(fileURLWithPath: venvPython)
            server.arguments = ["\(home)/bin/kokoro-server.py"]
            var senv = ProcessInfo.processInfo.environment
            senv["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
            server.environment = senv
            try? server.run()
            sleep(8)
        }
        
        // Register Option+Shift+A hotkey (no Accessibility needed)
        var hotKeyRef: EventHotKeyRef?
        let modifiers: UInt32 = UInt32(optionKey | shiftKey)
        let keyCode: UInt32 = 0x00
        let hotKeyID = EventHotKeyID(signature: OSType(0x564F5821), id: 1)
        RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)
        
        var eventSpec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let togglePath = toggleScript as NSString
        let togglePtr = UnsafeMutableRawPointer(mutating: togglePath.utf8String)
        InstallEventHandler(GetApplicationEventTarget(), { (_, event, userData) -> OSStatus in
            let path = String(cString: userData!.assumingMemoryBound(to: CChar.self))
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/bin/bash")
            task.arguments = [path]
            try? task.run()
            return noErr
        }, 1, &eventSpec, togglePtr, nil)
        
        // Launch the Python menu bar app
        let python = Process()
        python.executableURL = URL(fileURLWithPath: venvPython)
        python.arguments = [appScript]
        var env = ProcessInfo.processInfo.environment
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["VOX_NO_HOOK"] = "1"
        python.environment = env
        try? python.run()
        self.pythonProcess = python
        
        NSLog("[vox] Ready — Option+Shift+A to talk")
    }
    
    func applicationWillTerminate(_ notification: Notification) {
        pythonProcess?.terminate()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = VoxApp()
app.delegate = delegate
app.run()
