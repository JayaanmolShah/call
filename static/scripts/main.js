class VoiceChatApp {
  constructor() {
    this.ws = null;
    this.mediaRecorder = null;
    this.isRecording = false;
    this.recognition = null;

    this.recordButton = document.getElementById("recordButton");
    this.status = document.getElementById("status");
    this.conversation = document.getElementById("conversation");

    this.setupFileUpload();
    this.setupSpeechRecognition();
    this.setupEventListeners();
  }

  setupFileUpload() {
    const fileUpload = document.getElementById("fileUpload");
    const pdfInput = document.getElementById("pdfInput");
    const uploadStatus = document.getElementById("uploadStatus");

    ["dragenter", "dragover", "dragleave", "drop"].forEach(
      (eventName) => {
        fileUpload.addEventListener(eventName, (e) => {
          e.preventDefault();
          e.stopPropagation();
        });
      }
    );

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
      const response = await fetch(
        "http://127.0.0.1:8000/upload_knowledge",
        {
          method: "POST",
          body: formData,
        }
      );

      const result = await response.json();

      if (result.status === "success") {
        this.updateUploadStatus(
          "Knowledge base uploaded successfull✅!",
          "success"
        );
        this.recordButton.disabled = false;
        this.status.textContent =
          'Click "Start Recording" to begin conversation';
        this.initializeWebSocket();
      } else {
        this.updateUploadStatus(
          result.message || "Upload failed",
          "error"
        );
      }
    } catch (error) {
      console.error("Upload error:", error);
      this.updateUploadStatus("Failed to upload knowledge base", "error");
    }
  }

  updateUploadStatus(message, type) {
    const uploadStatus = document.getElementById("uploadStatus");
    uploadStatus.textContent = message;
    uploadStatus.className = `upload-status ${type}`;
  }

  setupSpeechRecognition() {
    if (!("webkitSpeechRecognition" in window)) {
      this.status.textContent =
        "Speech recognition is not supported in this browser.";
      this.recordButton.disabled = true;
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
        this.ws.send(
          JSON.stringify({
            action: "message",
            text: finalTranscript,
          })
        );
      }

      if (interimTranscript) {
        this.updateInterimText(interimTranscript);
      }
    };

    this.recognition.onerror = (event) => {
      console.error("Speech recognition error:", event.error);
      this.status.textContent = `Error: ${event.error}`;
    };

    this.recognition.onend = () => {
      if (this.isRecording) {
        this.recognition.start();
      }
    };
  }

  initializeWebSocket() {
    this.ws = new WebSocket("ws://127.0.0.1:8000/ws");

    this.ws.onopen = () => {
      console.log("WebSocket connection established");
    };

    this.ws.onmessage = async (event) => {
      const data = JSON.parse(event.data);

      if (data.type === "ai_response") {
        this.addMessage(data.text, "agent");
        if (data.audio) {
          const audio = new Audio("data:audio/wav;base64," + data.audio);
          await audio.play();
        }
      }
    };

    this.ws.onerror = (error) => {
      console.error("WebSocket error:", error);
      this.status.textContent =
        "Connection error. Please refresh the page.";
    };
  }

  setupEventListeners() {
    this.recordButton.addEventListener("click", () => {
      if (!this.isRecording) {
        this.startRecording();
      } else {
        this.stopRecording();
      }
    });
  }

  startRecording() {
    try {
      // First, send the start_recording action to trigger the greeting
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(
          JSON.stringify({
            action: "start_recording",
          })
        );
      }

      this.recognition.start();
      this.isRecording = true;
      this.recordButton.textContent = "Stop Recording";
      this.recordButton.classList.add("active");
      this.status.textContent = "Recording...";
    } catch (error) {
      console.error("Error starting recording:", error);
      this.status.textContent =
        "Error starting recording. Please check microphone permissions.";
    }
  }

  stopRecording() {
    this.recognition.stop();
    this.isRecording = false;
    this.recordButton.textContent = "Start Recording";
    this.recordButton.classList.remove("active");
    this.status.textContent = "Recording stopped";

    const interimElement = document.getElementById("interim");
    if (interimElement) {
      interimElement.remove();
    }
  }

  addMessage(text, sender) {
    const messageDiv = document.createElement("div");
    messageDiv.className = `message ${sender}-message`;
    messageDiv.textContent = text;
    this.conversation.appendChild(messageDiv);
    messageDiv.scrollIntoView({ behavior: "smooth" });
  }

  updateInterimText(text) {
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
}

// Initialize the app when the page loads
document.addEventListener("DOMContentLoaded", () => {
  new VoiceChatApp();
});
