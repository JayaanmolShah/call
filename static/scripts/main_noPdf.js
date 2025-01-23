const voiceDetectionWorkletCode = `
class VoiceDetectionProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.lastVoiceDetection = 0;
    this.voiceDetectionThreshold = -35; // dB
    this.debounceTime = 300; // ms
  }

  calculateDB(input) {
    let sum = 0;
    for (let i = 0; i < input.length; i++) {
      sum += input[i] * input[i];
    }
    const rms = Math.sqrt(sum / input.length);
    return 20 * Math.log10(rms);
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;

    const currentDB = this.calculateDB(input);
    const currentTime = currentTime();

    if (currentDB > this.voiceDetectionThreshold && 
        currentTime - this.lastVoiceDetection > this.debounceTime) {
      this.lastVoiceDetection = currentTime;
      this.port.postMessage({ userSpeaking: true });
    }

    return true;
  }
}

registerProcessor('voice-detection-processor', VoiceDetectionProcessor);
`;

class VoiceChatApp {
  constructor() {
    this.ws = null;
    this.mediaRecorder = null;
    this.isRecording = false;
    this.recognition = null;
    this.currentAudio = null;
    this.audioContext = null;
    this.audioAnalyser = null;
    this.audioWorklet = null;
    this.silenceThreshold = -50; // dB
    this.speakingThreshold = -35; // dB
    this.lastUserSpeakingTime = 0;
    this.userSpeakingDebounceTime = 300; // ms
    this.isAgentSpeaking = false;

    this.recordButton = document.getElementById("recordButton");
    this.status = document.getElementById("status");
    this.conversation = document.getElementById("conversation");

    // Enable recording by default
    if (this.recordButton) {
      this.recordButton.disabled = false;
      this.status.textContent = 'Click "Start Recording" to begin conversation';
    }

    this.setupFileUpload();
    this.setupSpeechRecognition();
    this.setupEventListeners();
    this.initializeAudioWorklet();

    // Initialize WebSocket connection immediately
    this.initializeWebSocket();
  }

  async initializeAudioWorklet() {
    try {
      this.audioContext = new (window.AudioContext ||
        window.webkitAudioContext)();
      this.audioAnalyser = this.audioContext.createAnalyser();
      this.audioAnalyser.fftSize = 2048;

      const blob = new Blob([voiceDetectionWorkletCode], {
        type: "text/javascript",
      });
      const workletUrl = URL.createObjectURL(blob);

      await this.audioContext.audioWorklet.addModule(workletUrl);
      this.audioWorklet = new AudioWorkletNode(
        this.audioContext,
        "voice-detection-processor"
      );

      this.audioWorklet.port.onmessage = (event) => {
        if (event.data.userSpeaking && this.isAgentSpeaking) {
          this.handleUserSpeaking();
        }
      };

      URL.revokeObjectURL(workletUrl);
    } catch (error) {
      console.error("Error initializing audio worklet:", error);
    }
  }

  setupFileUpload() {
    const fileUpload = document.getElementById("fileUpload");
    const pdfInput = document.getElementById("pdfInput");

    if (!fileUpload || !pdfInput) {
      console.log(
        "File upload elements not found - continuing without file upload functionality"
      );
      return;
    }

    ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
      fileUpload.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      fileUpload.addEventListener(eventName, () => {
        fileUpload.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      fileUpload.addEventListener(eventName, () => {
        fileUpload.classList.remove("dragover");
      });
    });

    fileUpload.addEventListener("drop", (e) => {
      const file = e.dataTransfer.files[0];
      if (file) this.handleFileUpload(file);
    });

    pdfInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file) this.handleFileUpload(file);
    });
  }

  async handleFileUpload(file) {
    if (file.type !== "application/pdf") {
      this.updateUploadStatus("Please upload a PDF file", "error");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch("http://127.0.0.1:8000/upload_knowledge", {
        method: "POST",
        body: formData,
      });

      const result = await response.json();

      if (result.status === "success") {
        this.updateUploadStatus(
          "Knowledge base updated successfully✅!",
          "success"
        );
        // Reinitialize WebSocket to use new knowledge base
        this.initializeWebSocket();
      } else {
        this.updateUploadStatus(result.message || "Upload failed", "error");
      }
    } catch (error) {
      console.error("Upload error:", error);
      this.updateUploadStatus("Failed to upload knowledge base", "error");
    }
  }

  updateUploadStatus(message, type) {
    const uploadStatus = document.getElementById("uploadStatus");
    if (uploadStatus) {
      uploadStatus.textContent = message;
      uploadStatus.className = `upload-status ${type}`;
    }
  }

  setupSpeechRecognition() {
    if (!("webkitSpeechRecognition" in window)) {
      if (this.status) {
        this.status.textContent =
          "Speech recognition is not supported in this browser.";
      }
      if (this.recordButton) {
        this.recordButton.disabled = true;
      }
      return;
    }

    this.recognition = new webkitSpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;

    this.recognition.onresult = (event) => {
      let interimTranscript = "";
      let finalTranscript = "";

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += transcript;
        } else {
          interimTranscript += transcript;
        }
      }

      if (finalTranscript) {
        this.addMessage(finalTranscript, "user");
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(
            JSON.stringify({
              action: "message",
              text: finalTranscript,
            })
          );
        }
      }

      if (interimTranscript) {
        this.updateInterimText(interimTranscript);
      }
    };

    this.recognition.onerror = (event) => {
      console.error("Speech recognition error:", event.error);
      if (this.status) {
        this.status.textContent = `Error: ${event.error}`;
      }
    };

    this.recognition.onend = () => {
      if (this.isRecording) {
        this.recognition.start();
      }
    };
  }

  handleUserSpeaking() {
    const now = Date.now();
    if (now - this.lastUserSpeakingTime > this.userSpeakingDebounceTime) {
      this.lastUserSpeakingTime = now;

      this.stopCurrentAudio();

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(
          JSON.stringify({
            action: "user_speaking",
            timestamp: now,
          })
        );
      }
    }
  }

  stopCurrentAudio() {
    if (this.currentAudio) {
      this.isAgentSpeaking = false;
      this.currentAudio.pause();
      this.currentAudio.currentTime = 0;
      this.currentAudio = null;
    }
  }

  initializeWebSocket() {
    // Close existing connection if any
    if (this.ws) {
      this.ws.close();
    }

    this.ws = new WebSocket("ws://127.0.0.1:8000/ws");

    this.ws.onopen = () => {
      console.log("WebSocket connection established");
    };

    this.ws.onmessage = async (event) => {
      const data = JSON.parse(event.data);

      if (data.type === "ai_response") {
        this.addMessage(data.text, "agent");

        if (data.audio) {
          this.stopCurrentAudio();

          const audio = new Audio("data:audio/wav;base64," + data.audio);
          this.currentAudio = audio;

          audio.addEventListener("play", () => {
            this.isAgentSpeaking = true;
            if (this.status) {
              this.status.textContent = "Agent speaking...";
            }
          });

          audio.addEventListener("ended", () => {
            this.isAgentSpeaking = false;
            this.currentAudio = null;
            if (this.status) {
              this.status.textContent = "Recording...";
            }
          });

          try {
            await audio.play();
          } catch (error) {
            console.error("Error playing audio:", error);
          }
        }

        if (data.end_call) {
          this.stopRecording();
          if (this.ws) {
            this.ws.close();
          }
        }
      }
    };

    this.ws.onerror = (error) => {
      console.error("WebSocket error:", error);
      if (this.status) {
        this.status.textContent = "Connection error. Please refresh the page.";
      }
    };

    this.ws.onclose = () => {
      console.log("WebSocket connection closed");
    };
  }

  async startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaStreamSource =
        this.audioContext.createMediaStreamSource(stream);

      mediaStreamSource
        .connect(this.audioAnalyser)
        .connect(this.audioWorklet)
        .connect(this.audioContext.destination);

      if (this.audioContext.state === "suspended") {
        await this.audioContext.resume();
      }

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(
          JSON.stringify({
            action: "start_recording",
          })
        );
      }

      this.recognition.start();
      this.isRecording = true;
      if (this.recordButton) {
        this.recordButton.textContent = "Stop Recording";
        this.recordButton.classList.add("active");
      }
      if (this.status) {
        this.status.textContent = "Recording...";
      }
    } catch (error) {
      console.error("Error starting recording:", error);
      if (this.status) {
        this.status.textContent =
          "Error starting recording. Please check microphone permissions.";
      }
    }
  }

  stopRecording() {
    this.recognition.stop();
    this.isRecording = false;
    if (this.recordButton) {
      this.recordButton.textContent = "Start Recording";
      this.recordButton.classList.remove("active");
    }
    if (this.status) {
      this.status.textContent = "Recording stopped";
    }

    if (this.audioContext && this.audioContext.state === "running") {
      this.audioContext.suspend();
    }

    this.stopCurrentAudio();

    const interimElement = document.getElementById("interim");
    if (interimElement) {
      interimElement.remove();
    }
  }

  addMessage(text, sender) {
    if (!this.conversation) return;

    const messageDiv = document.createElement("div");
    messageDiv.className = `message ${sender}-message`;
    messageDiv.textContent = text;
    this.conversation.appendChild(messageDiv);
    messageDiv.scrollIntoView({ behavior: "smooth" });
  }

  updateInterimText(text) {
    if (!this.conversation) return;

    let interimElement = document.getElementById("interim");
    if (!interimElement) {
      interimElement = document.createElement("div");
      interimElement.id = "interim";
      interimElement.className = "message user-message interim";
      this.conversation.appendChild(interimElement);
    }
    interimElement.textContent = text;
    interimElement.scrollIntoView({ behavior: "smooth" });
  }

  setupEventListeners() {
    if (this.recordButton) {
      this.recordButton.addEventListener("click", () => {
        if (!this.isRecording) {
          this.startRecording();
        } else {
          this.stopRecording();
        }
      });
    }
  }
}

// Initialize the app when the page loads
document.addEventListener("DOMContentLoaded", () => {
  new VoiceChatApp();
});