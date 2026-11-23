// sofos-screen: transmite la pantalla de un iPhone conectado por USB.
//
// Usa el mismo mecanismo que QuickTime («Nueva grabación de película» → iPhone):
// macOS expone la pantalla del iPhone como un dispositivo de captura. No requiere
// modo desarrollador. Sólo se ve la pantalla; el iPhone no permite controlarlo así.
//
// Uso: sofos-screen <UDID> [alto_max] [detectar_sonido 0|1]
// Salida (stdout): frames JPEG, cada uno precedido por su largo (UInt32 big-endian).
// Estado (stderr): líneas "STATUS <texto>" / "ERROR <texto>".
// Con detectar_sonido=1, apenas el iPhone reproduce algo escribe "STATUS SOUND" y sale,
// para que el sonido vuelva al iPhone. Si stdin se cierra (Sofos terminó), sale también.

import AVFoundation
import CoreImage
import CoreMediaIO
import Foundation

setvbuf(stderr, nil, _IONBF, 0)
signal(SIGPIPE, SIG_IGN)

func status(_ s: String) { FileHandle.standardError.write("STATUS \(s)\n".data(using: .utf8)!) }
func fail(_ s: String, code: Int32) -> Never {
    FileHandle.standardError.write("ERROR \(s)\n".data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("uso: sofos-screen <UDID> [alto_max]", code: 64) }
let wantedID = args[1].replacingOccurrences(of: "-", with: "").lowercased()
let maxHeight = args.count >= 3 ? Double(args[2]) ?? 1600 : 1600
let detectSound = args.count >= 4 ? args[3] != "0" : true

// 1. Pedirle a macOS que exponga las pantallas de iPhone/iPad como dispositivos de captura.
var address = CMIOObjectPropertyAddress(
    mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
    mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
    mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
var allow: UInt32 = 1
CMIOObjectSetPropertyData(
    CMIOObjectID(kCMIOObjectSystemObject), &address, 0, nil,
    UInt32(MemoryLayout<UInt32>.size), &allow)

// 2. Permiso de cámara: macOS lo trata como cámara y lo pregunta una vez.
func ensureAccess() {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized: return
    case .notDetermined:
        status("Pidiendo permiso de cámara a macOS…")
        let sem = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .video) { ok in granted = ok; sem.signal() }
        sem.wait()
        if !granted { fail("Sin permiso de cámara.", code: 3) }
    default:
        fail("macOS bloqueó el permiso de cámara. Actívalo en Ajustes del Sistema → Privacidad y seguridad → Cámara.", code: 3)
    }
}
ensureAccess()

// 3. Esperar a que aparezca la pantalla del iPhone (tarda 1-3 s tras habilitarla).
func findDevice() -> AVCaptureDevice? {
    let found = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.external], mediaType: .muxed, position: .unspecified).devices
    if let exact = found.first(where: {
        $0.uniqueID.replacingOccurrences(of: "-", with: "").lowercased() == wantedID
    }) { return exact }
    let iosScreens = found.filter { $0.modelID == "iOS Device" }
    return iosScreens.count == 1 ? iosScreens[0] : nil
}

status("Buscando la pantalla del iPhone…")
var device: AVCaptureDevice?
let deadline = Date().addingTimeInterval(15)
while device == nil && Date() < deadline {
    RunLoop.current.run(until: Date().addingTimeInterval(0.3))
    device = findDevice()
}
guard let device else {
    fail("No apareció la pantalla del iPhone. Desbloquéalo y vuelve a intentar.", code: 2)
}

// 4. Capturar y emitir JPEG.
final class FrameWriter: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let context = CIContext(options: [.cacheIntermediates: false])
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    var frames = 0

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let pixels = CMSampleBufferGetImageBuffer(sample) else { return }
        var image = CIImage(cvPixelBuffer: pixels)
        let scale = min(1.0, maxHeight / image.extent.height)
        if scale < 1 { image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale)) }
        guard let jpeg = context.jpegRepresentation(
            of: image, colorSpace: colorSpace,
            options: [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): 0.7])
        else { return }
        var length = UInt32(jpeg.count).bigEndian
        let header = Data(bytes: &length, count: 4)
        for chunk in [header, jpeg] {
            let ok = chunk.withUnsafeBytes { buf -> Bool in
                var offset = 0
                while offset < buf.count {
                    let n = write(STDOUT_FILENO, buf.baseAddress! + offset, buf.count - offset)
                    if n <= 0 { return false }
                    offset += n
                }
                return true
            }
            if !ok { exit(0) }  // Sofos cerró la conexión
        }
        frames += 1
        if frames == 1 { status("STREAMING \(Int(image.extent.width))x\(Int(image.extent.height))") }
    }
}

// Mientras se captura su pantalla, el iPhone manda su sonido por el cable (iOS lo decide así,
// no se puede evitar). Para que el sonido siga en el iPhone, escuchamos ese canal y, apenas
// deja de ser silencio, cerramos la captura: iOS devuelve el sonido al iPhone.
let session = AVCaptureSession()
let input: AVCaptureDeviceInput
do {
    input = try AVCaptureDeviceInput(device: device)
} catch {
    fail("No se pudo abrir la pantalla del iPhone: \(error.localizedDescription)", code: 4)
}
guard session.canAddInput(input) else { fail("No se pudo usar la pantalla del iPhone.", code: 4) }
session.addInputWithNoConnections(input)
for port in input.ports where port.mediaType != .video && port.mediaType != .audio {
    port.isEnabled = false
}
guard let videoPort = input.ports.first(where: { $0.mediaType == .video }) else {
    fail("La pantalla del iPhone no entregó video.", code: 4)
}

let output = AVCaptureVideoDataOutput()
output.alwaysDiscardsLateVideoFrames = true
output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
let writer = FrameWriter()
output.setSampleBufferDelegate(writer, queue: DispatchQueue(label: "frames"))
guard session.canAddOutput(output) else { fail("No se pudo capturar video.", code: 4) }
session.addOutputWithNoConnections(output)

let connection = AVCaptureConnection(inputPorts: [videoPort], output: output)
guard session.canAddConnection(connection) else { fail("No se pudo conectar el video.", code: 4) }
session.addConnection(connection)

// Detector de sonido. Leer el audio cuenta como micrófono para macOS (permiso aparte, una vez).
// El video NUNCA espera por esto: el permiso se pide en segundo plano.
final class SoundDetector: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    static let threshold: Float = 0.003  // ~ -50 dBFS: por encima de esto ya no es silencio
    static let buffersNeeded = 3         // ~30-100 ms seguidos, para ignorar chasquidos sueltos
    var loudBuffers = 0

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let block = CMSampleBufferGetDataBuffer(sample) else { return }
        var length = 0
        var pointer: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil,
                                          totalLengthOut: &length, dataPointerOut: &pointer) == noErr,
              let pointer else { return }
        let samples = UnsafeBufferPointer(
            start: UnsafeRawPointer(pointer).assumingMemoryBound(to: Float32.self),
            count: length / MemoryLayout<Float32>.size)
        let peak = samples.reduce(Float(0)) { max($0, abs($1)) }
        loudBuffers = peak > SoundDetector.threshold ? loudBuffers + 1 : 0
        if loudBuffers >= SoundDetector.buffersNeeded {
            status("SOUND")  // Sofos pausa la pantalla
            session.stopRunning()
            exit(0)  // al soltar la captura, iOS devuelve el sonido al iPhone
        }
    }
}

let detector = SoundDetector()
var audioAttached = false

func attachDetector() {
    guard !audioAttached, let audioPort = input.ports.first(where: { $0.mediaType == .audio }) else {
        if !audioAttached { status("AUDIO no disponible") }
        return
    }
    let audioOutput = AVCaptureAudioDataOutput()
    audioOutput.audioSettings = [  // Float32 intercalado: fácil de medir
        AVFormatIDKey: kAudioFormatLinearPCM,
        AVLinearPCMIsFloatKey: true,
        AVLinearPCMBitDepthKey: 32,
        AVLinearPCMIsNonInterleaved: false,
    ]
    audioOutput.setSampleBufferDelegate(detector, queue: DispatchQueue(label: "sound"))
    session.beginConfiguration()
    defer { session.commitConfiguration() }
    guard session.canAddOutput(audioOutput) else { status("AUDIO no disponible"); return }
    session.addOutputWithNoConnections(audioOutput)
    let audioConnection = AVCaptureConnection(inputPorts: [audioPort], output: audioOutput)
    guard session.canAddConnection(audioConnection) else {
        session.removeOutput(audioOutput); status("AUDIO no disponible"); return
    }
    session.addConnection(audioConnection)
    audioAttached = true
    status("AUDIO vigilando")
}

func startDetector() {  // en el hilo principal
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        attachDetector()
    case .notDetermined:
        status("AUDIO pidiendo permiso")
        AVCaptureDevice.requestAccess(for: .audio) { ok in
            DispatchQueue.main.async { if ok { attachDetector() } else { status("AUDIO sin permiso") } }
        }
    default:
        status("AUDIO sin permiso")
    }
}

// Si Sofos termina (stdin se cierra), salimos: el ayudante nunca queda huérfano.
Thread {
    while readLine() != nil {}
    exit(0)
}.start()
status("Conectando con \(device.localizedName)…")
session.startRunning()
if detectSound { DispatchQueue.main.async { startDetector() } }
RunLoop.main.run()
