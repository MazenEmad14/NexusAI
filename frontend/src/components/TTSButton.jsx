import React, { useState, useRef, useEffect } from 'react';
import { Volume2, Loader2 } from 'lucide-react';
import { generateSpeech } from '../services/api';

export default function TTSButton({ text, autoPlay = false, onPlayStateChange }) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const audioRef = useRef(null);

  useEffect(() => {
    if (autoPlay && text && !isPlaying && !isLoading && !audioRef.current) {
      handlePlayAudio();
    }
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        if (audioRef.current.src) URL.revokeObjectURL(audioRef.current.src);
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoPlay, text]);

  const handlePlayAudio = async () => {
    if (isPlaying && audioRef.current) {
      audioRef.current.pause();
      setIsPlaying(false);
      if (onPlayStateChange) onPlayStateChange(false);
      return;
    }

    try {
      setIsLoading(true);
      const url = await generateSpeech(text);
      const audio = new Audio(url);
      audioRef.current = audio;

      audio.onended = () => {
        setIsPlaying(false);
        URL.revokeObjectURL(url);
        if (onPlayStateChange) onPlayStateChange(false);
      };

      audio.play();
      setIsPlaying(true);
      if (onPlayStateChange) onPlayStateChange(true);
    } catch (err) {
      console.error('TTS Playback failed:', err);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <button
      onClick={handlePlayAudio}
      disabled={isLoading}
      className={`p-1.5 rounded-md transition-colors border border-white/5 cursor-pointer flex items-center justify-center shrink-0 ${
        isPlaying ? 'bg-[#00f0ff]/20 text-[#00f0ff]' : 'bg-white/[0.03] text-white/40 hover:text-white/80 hover:bg-white/10'
      }`}
      title="Play Audio"
    >
      {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Volume2 className="w-4 h-4" />}
    </button>
  );
}
