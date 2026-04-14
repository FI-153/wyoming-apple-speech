import AVFoundation

/// Standard 16 kHz, 16-bit signed-integer mono format used by the Wyoming protocol.
let pcmInputFormat = AVAudioFormat(
    commonFormat: .pcmFormatInt16,
    sampleRate: 16000,
    channels: 1,
    interleaved: true
)!

/// Create an ``AVAudioPCMBuffer`` from raw PCM data in ``pcmInputFormat``.
///
/// Returns `nil` when the data contains zero frames (empty audio).
///
/// - Parameter pcmData: Raw PCM audio bytes (16 kHz, 16-bit signed integer, mono).
/// - Returns: A filled buffer, or `nil` if the data is empty.
func makePCMBuffer(from pcmData: Data) throws -> AVAudioPCMBuffer? {
    let bytesPerFrame = pcmInputFormat.streamDescription.pointee.mBytesPerFrame
    let frameCount = UInt32(pcmData.count) / bytesPerFrame
    guard frameCount > 0 else {
        return nil
    }

    guard let buffer = AVAudioPCMBuffer(
        pcmFormat: pcmInputFormat,
        frameCapacity: frameCount
    ) else {
        throw STTError.bufferCreationFailed
    }
    buffer.frameLength = frameCount

    pcmData.withUnsafeBytes { rawBuffer in
        guard let src = rawBuffer.baseAddress else { return }
        memcpy(buffer.int16ChannelData![0], src, pcmData.count)
    }

    return buffer
}
