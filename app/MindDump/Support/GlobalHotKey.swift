import AppKit
import Carbon.HIToolbox

/// One key combination that reaches the app from inside any other app (D23).
///
/// It uses macOS's hot key API (`RegisterEventHotKey`, from Carbon's HIToolbox): the app
/// tells the system "this one combination is mine", and the system sends the app an event
/// when it is pressed, whichever app is in front. The app never sees any other key, so no
/// permission is asked (a key event monitor would see every key and needs Accessibility).
///
/// Think of it as a doorbell: the system rings only when someone presses our button; it
/// does not hand us a recording of the whole street.
///
/// This file knows nothing about the island. It calls `onPress` once per press, and keeps
/// the Carbon types out of the rest of the app.
@MainActor
final class GlobalHotKey {
    struct Combination {
        let keyCode: UInt32
        let modifiers: UInt32
        let name: String
    }

    /// The default (D23). One constant, so a settings screen can replace it later.
    /// Option-Space is taken by other launchers; Option-Shift-Space is not a macOS default.
    static let optionShiftSpace = Combination(
        keyCode: UInt32(kVK_Space), modifiers: UInt32(optionKey | shiftKey), name: "Option-Shift-Space"
    )

    /// What a press does. Called on the main actor, once per press (held keys do not repeat).
    var onPress: (() -> Void)?

    /// Tells our hot key apart from any other the app might register later ("MDhk").
    nonisolated fileprivate static let signature: OSType = 0x4D44_686B

    private let combination: Combination
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?

    /// Held down: macOS sends the press again as the key repeats. Only the first counts.
    private var isDown = false
    private var lastPress = ContinuousClock.now

    init(_ combination: Combination = GlobalHotKey.optionShiftSpace) {
        self.combination = combination
    }

    /// Install the handler, then register the combination. Exactly once; on failure the app
    /// keeps running without the shortcut and says so in one line.
    func register() {
        guard handler == nil else { return }
        var types = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)),
        ]
        // The handler gets `self` back as a raw pointer. Unretained is safe: `unregister`
        // removes the handler before this object can go away (the app delegate owns it
        // for the app's whole life).
        let me = Unmanaged.passUnretained(self).toOpaque()
        let installed = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, event, userData in GlobalHotKey.received(event, userData) },
            types.count, &types, me, &handler
        )
        guard installed == noErr else {
            handler = nil
            Self.log("[hotkey] could not install the handler, status \(installed): \(combination.name) will do nothing")
            return
        }
        let id = EventHotKeyID(signature: Self.signature, id: 1)
        // Exclusive: if another app already holds this combination exclusively, fail and
        // say so, rather than share it.
        let status = RegisterEventHotKey(
            combination.keyCode, combination.modifiers, id, GetApplicationEventTarget(),
            OptionBits(kEventHotKeyExclusive), &hotKey
        )
        if status == noErr {
            Self.log("[hotkey] registered \(combination.name), status \(status)")
        } else {
            hotKey = nil
            Self.log("[hotkey] could not register \(combination.name), status \(status) (another app may hold it); the shortcut is off")
        }
    }

    /// Give the combination back to the system. Called when the app quits.
    func unregister() {
        let unregistered = hotKey.map { UnregisterEventHotKey($0) }
        let removed = handler.map { RemoveEventHandler($0) }
        hotKey = nil
        handler = nil
        Self.debugLog("[hotkey] unregistered, status \(unregistered.map(String.init) ?? "-"), handler removed, status \(removed.map(String.init) ?? "-")")
    }

    enum Edge { case pressed, released }

    /// A press or release of the combination. The system's event comes here, and so does a
    /// debug script's `hotkey` verb, so a script runs everything after the event's delivery.
    func handle(_ edge: Edge) {
        switch edge {
        case .released:
            isDown = false
        case .pressed:
            let now = ContinuousClock.now
            defer { lastPress = now }
            // A repeat comes while the keys are still down, within the key repeat delay
            // (first) or interval (later) of the press before. Checking the gap too means a
            // lost release cannot leave the shortcut stuck: the next real press is later.
            let gap = isDown ? now - lastPress : .seconds(1_000)
            let slack = Duration.seconds(max(NSEvent.keyRepeatDelay, NSEvent.keyRepeatInterval) + 0.25)
            if isDown, gap < slack {
                Self.debugLog("[hotkey] repeat ignored")
                return
            }
            isDown = true
            onPress?()
        }
    }

    /// The C callback. The application event target delivers on the main thread, in the
    /// main run loop, so this is on the main actor already; `assumeIsolated` checks that,
    /// and anything else is sent to the main queue rather than trusted.
    private nonisolated static func received(_ event: EventRef?, _ userData: UnsafeMutableRawPointer?) -> OSStatus {
        guard let event, let userData else { return OSStatus(eventNotHandledErr) }
        var id = EventHotKeyID()
        let status = GetEventParameter(
            event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
            nil, MemoryLayout<EventHotKeyID>.size, nil, &id
        )
        guard status == noErr, id.signature == signature else { return OSStatus(eventNotHandledErr) }
        let edge: Edge = GetEventKind(event) == UInt32(kEventHotKeyPressed) ? .pressed : .released
        let hotKey = Unmanaged<GlobalHotKey>.fromOpaque(userData).takeUnretainedValue()
        if Thread.isMainThread {
            MainActor.assumeIsolated { hotKey.systemDelivered(edge) }
        } else {
            DispatchQueue.main.async { hotKey.systemDelivered(edge) }
        }
        return noErr
    }

    /// Only a real key press reaches this line: the proof, in a debug log, that the system
    /// delivered the event.
    private func systemDelivered(_ edge: Edge) {
        Self.debugLog(edge == .pressed ? "[hotkey] pressed" : "[hotkey] released")
        handle(edge)
    }

    /// Registration lines go to the log in every build (the unified log in Release), so a
    /// shortcut that does nothing can be explained.
    private static func log(_ line: String) {
        #if DEBUG
        print(line)
        fflush(stdout)
        #else
        NSLog("%@", line)
        #endif
    }

    private static func debugLog(_ line: String) {
        #if DEBUG
        print(line)
        fflush(stdout)
        #endif
    }
}
