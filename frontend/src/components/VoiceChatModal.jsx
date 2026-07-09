import React, { useState, useEffect, useRef, useCallback } from 'react';
import './VoiceChatModal.css';
import { X, Mic, Square } from 'lucide-react';
import { transcribeAudio, sendChatMessage, generateSpeech } from '../services/api';

const STATES = {
  IDLE: 'IDLE',
  LISTENING: 'LISTENING',
  PROCESSING: 'PROCESSING (Transcribing)',
  THINKING: 'THINKING (Generating)',
  SPEAKING: 'SPEAKING'
};

export default function VoiceChatModal({ isOpen, onClose, projectId, threadId }) {
  const [currentState, setCurrentState] = useState(STATES.IDLE);
  const [transcript, setTranscript] = useState('');
  
  const canvasRef = useRef(null);
  const audioRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const streamRef = useRef(null);
  
  // Single persistent AudioContext to prevent "HTMLMediaElement already connected" errors
  const sharedAudioCtxRef = useRef(null);
  const analyserRef = useRef(null);
  const micSourceRef = useRef(null);
  const playbackSourceRef = useRef(null);
  const requestAnimationFrameRef = useRef(null);

  const initSharedAudioCtx = () => {
    if (!sharedAudioCtxRef.current || sharedAudioCtxRef.current.state === 'closed') {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      sharedAudioCtxRef.current = new Ctx();
    }
    return sharedAudioCtxRef.current;
  };

  const cleanup = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
    }
    if (requestAnimationFrameRef.current) {
      cancelAnimationFrame(requestAnimationFrameRef.current);
    }
    setCurrentState(STATES.IDLE);
    setTranscript('');
  }, []);

  useEffect(() => {
    if (!isOpen) {
      cleanup();
    }
  }, [isOpen, cleanup]);

  const startVisualizer = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const baseRadius = rect.width * 0.2;
    
    let time = 0;

    const draw = () => {
      requestAnimationFrameRef.current = requestAnimationFrame(draw);
      
      let averageVolume = 0;
      
      if (analyserRef.current) {
        const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
        analyserRef.current.getByteFrequencyData(dataArray);
        
        let sum = 0;
        for(let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        averageVolume = sum / dataArray.length;
      }
      
      ctx.clearRect(0, 0, rect.width, rect.height);
      
      let rgb = '0, 200, 255'; 
      if (currentState === STATES.LISTENING) rgb = '0, 255, 150'; 
      else if (currentState === STATES.SPEAKING) rgb = '200, 50, 255'; 
      else if (currentState === STATES.THINKING || currentState === STATES.PROCESSING) rgb = '255, 180, 0'; 
      
      const dynamicRadius = baseRadius + (averageVolume * 0.6);
      
      const gradient = ctx.createRadialGradient(centerX, centerY, baseRadius * 0.5, centerX, centerY, dynamicRadius * 1.5);
      gradient.addColorStop(0, `rgba(${rgb}, 0.8)`);
      gradient.addColorStop(0.4, `rgba(${rgb}, 0.3)`);
      gradient.addColorStop(1, `rgba(${rgb}, 0)`);
      
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(centerX, centerY, dynamicRadius * 1.5, 0, Math.PI * 2);
      ctx.fill();
      
      ctx.beginPath();
      ctx.arc(centerX, centerY, baseRadius * 0.8 + (Math.sin(time) * 5), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 255, 255, 0.9)`;
      ctx.fill();
      
      if (averageVolume > 30) {
        ctx.beginPath();
        ctx.arc(centerX, centerY, dynamicRadius * 1.2, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${rgb}, 0.5)`;
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      
      time += 0.05;
    };
    
    draw();
  }, [currentState]);

  useEffect(() => {
    if (isOpen) {
      startVisualizer();
    }
  }, [isOpen, startVisualizer]);

  const handleStartListening = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      
      const audioCtx = initSharedAudioCtx();
      
      if (!analyserRef.current) {
        analyserRef.current = audioCtx.createAnalyser();
        analyserRef.current.fftSize = 256;
      }
      
      if (micSourceRef.current) {
        micSourceRef.current.disconnect();
      }
      
      micSourceRef.current = audioCtx.createMediaStreamSource(stream);
      micSourceRef.current.connect(analyserRef.current);
      
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];
      
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      
      mediaRecorder.onstop = async () => {
        if (micSourceRef.current) {
          micSourceRef.current.disconnect();
        }
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        processAudioInteraction(audioBlob);
      };
      
      mediaRecorder.start();
      setCurrentState(STATES.LISTENING);
      setTranscript('Listening...');
      
    } catch (err) {
      console.error('Mic error:', err);
      setTranscript('Microphone access denied.');
    }
  };

  const handleStopListening = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
    }
  };

  const processAudioInteraction = async (audioBlob) => {
    try {
      setCurrentState(STATES.PROCESSING);
      const text = await transcribeAudio(audioBlob);
      setTranscript(text);
      
      if (!text || text.trim() === '') {
        setCurrentState(STATES.IDLE);
        return;
      }
      
      setCurrentState(STATES.THINKING);
      const chatRes = await sendChatMessage(text, projectId, threadId);
      
      let aiResponseText = '';
      if (chatRes && chatRes.response) {
        aiResponseText = chatRes.response;
      } else if (chatRes && chatRes.data) {
        aiResponseText = typeof chatRes.data === 'string' ? chatRes.data : chatRes.data.response || '';
      }
      
      if (!aiResponseText) {
        setTranscript('Empty response from AI.');
        setCurrentState(STATES.IDLE);
        return;
      }
      
      setTranscript(aiResponseText);
      
      setCurrentState(STATES.THINKING);
      const audioUrl = await generateSpeech(aiResponseText);
      
      setCurrentState(STATES.SPEAKING);
      playAiAudio(audioUrl);
      
    } catch (error) {
      console.error('Voice interaction error:', error);
      setTranscript('An error occurred during communication.');
      setCurrentState(STATES.IDLE);
    }
  };

  const playAiAudio = (audioUrl) => {
    if (!audioRef.current) return;
    
    const audioCtx = initSharedAudioCtx();
    
    if (!analyserRef.current) {
      analyserRef.current = audioCtx.createAnalyser();
      analyserRef.current.fftSize = 256;
    }
    
    if (!playbackSourceRef.current) {
      playbackSourceRef.current = audioCtx.createMediaElementSource(audioRef.current);
    }
    
    try { playbackSourceRef.current.disconnect(); } catch(e) {}
    try { analyserRef.current.disconnect(); } catch(e) {}
    
    playbackSourceRef.current.connect(analyserRef.current);
    analyserRef.current.connect(audioCtx.destination);
    
    audioRef.current.src = audioUrl;
    audioRef.current.play().catch(e => console.error("Audio play failed:", e));
    
    audioRef.current.onended = () => {
      setCurrentState(STATES.IDLE);
      setTranscript('');
      
      try { playbackSourceRef.current.disconnect(); } catch(e) {}
      try { analyserRef.current.disconnect(); } catch(e) {}
      
      URL.revokeObjectURL(audioUrl);
    };
  };

  if (!isOpen) return null;

  return (
    <div className="voice-chat-modal-overlay">
      <div className="voice-chat-modal-content">
        <button className="voice-modal-close" onClick={onClose}>
          <X size={28} />
        </button>
        
        <div className="voice-status-badge">
          {currentState}
        </div>
        
        <div className="voice-visualizer-container">
          <canvas ref={canvasRef} className="voice-canvas"></canvas>
        </div>
        
        <div className="voice-subtitle">
          {transcript}
        </div>
        
        <div className="voice-controls">
          {currentState === STATES.IDLE ? (
            <button className="voice-action-btn pulse-btn" onClick={handleStartListening}>
              <Mic size={32} />
            </button>
          ) : currentState === STATES.LISTENING ? (
            <button className="voice-action-btn stop-btn" onClick={handleStopListening}>
              <Square size={32} />
            </button>
          ) : (
            <button className="voice-action-btn disabled-btn" disabled>
              <Mic size={32} />
            </button>
          )}
        </div>
        
        <audio ref={audioRef} crossOrigin="anonymous" style={{ display: 'none' }} />
      </div>
    </div>
  );
}
