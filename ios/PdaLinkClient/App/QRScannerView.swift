import AVFoundation
import SwiftUI
import UIKit

enum QRScannerError: LocalizedError {
    case cameraDenied
    case cameraUnavailable
    case inputUnavailable

    var errorDescription: String? {
        switch self {
        case .cameraDenied:
            return "Camera access is required to scan the PdaLink QR code. You can paste the connection URI instead."
        case .cameraUnavailable:
            return "This device does not have an available camera."
        case .inputUnavailable:
            return "The camera could not be configured for QR scanning."
        }
    }
}

struct QRScannerView: UIViewControllerRepresentable {
    let completion: (Result<String, Error>) -> Void

    func makeUIViewController(context: Context) -> QRScannerViewController {
        QRScannerViewController(completion: completion)
    }

    func updateUIViewController(_ uiViewController: QRScannerViewController, context: Context) {}
}

final class QRScannerViewController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    private let completion: (Result<String, Error>) -> Void
    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "dev.pdalink.client.qr-session")
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var hasCompleted = false

    init(completion: @escaping (Result<String, Error>) -> Void) {
        self.completion = completion
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        authorizeAndConfigure()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        stopSession()
    }

    private func authorizeAndConfigure() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            configureSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    if granted {
                        self?.configureSession()
                    } else {
                        self?.complete(.failure(QRScannerError.cameraDenied))
                    }
                }
            }
        default:
            complete(.failure(QRScannerError.cameraDenied))
        }
    }

    private func configureSession() {
        guard let camera = AVCaptureDevice.default(for: .video) else {
            complete(.failure(QRScannerError.cameraUnavailable))
            return
        }
        do {
            let input = try AVCaptureDeviceInput(device: camera)
            guard session.canAddInput(input) else {
                throw QRScannerError.inputUnavailable
            }
            session.addInput(input)

            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else {
                throw QRScannerError.inputUnavailable
            }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = [.qr]

            let preview = AVCaptureVideoPreviewLayer(session: session)
            preview.videoGravity = .resizeAspectFill
            preview.frame = view.bounds
            view.layer.addSublayer(preview)
            previewLayer = preview

            sessionQueue.async { [weak self] in
                self?.session.startRunning()
            }
        } catch {
            complete(.failure(error))
        }
    }

    func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        guard let object = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              object.type == .qr,
              let value = object.stringValue else {
            return
        }
        complete(.success(value))
    }

    private func complete(_ result: Result<String, Error>) {
        guard !hasCompleted else { return }
        hasCompleted = true
        stopSession()
        completion(result)
    }

    private func stopSession() {
        sessionQueue.async { [weak self] in
            guard let self, self.session.isRunning else { return }
            self.session.stopRunning()
        }
    }
}
