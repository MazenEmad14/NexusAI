import { useState, useRef, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Upload, FileText, Loader2, CheckCircle2, Database, Trash2 } from 'lucide-react';
import DashboardPanel from './DashboardPanel';
import { uploadFile, processFiles, pushIndex } from '../services/api';

export default function KnowledgePanel({ projectId = 'default', onToast }) {
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [processingAll, setProcessingAll] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const fileInputRef = useRef(null);

  const handleUpload = useCallback(async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    try {
      const result = await uploadFile(file, projectId);
      const newFile = {
        name: file.name,
        size: (file.size / 1024).toFixed(1) + ' KB',
        fileId: result.file_id,
        status: 'uploaded',
      };
      setFiles(prev => [...prev, newFile]);
      onToast?.('success', `📄 "${file.name}" uploaded successfully`);

      // Auto-process
      try {
        await processFiles(projectId, { fileId: result.file_id });
        setFiles(prev => prev.map(f =>
          f.fileId === result.file_id ? { ...f, status: 'processed' } : f
        ));
      } catch {
        // Processing can fail if vector DB isn't configured
        setFiles(prev => prev.map(f =>
          f.fileId === result.file_id ? { ...f, status: 'uploaded (unprocessed)' } : f
        ));
      }
    } catch (err) {
      onToast?.('error', `Upload failed: ${err.message}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [projectId, onToast]);

  const handlePushIndex = useCallback(async () => {
    setIndexing(true);
    try {
      const result = await pushIndex(projectId, false);
      onToast?.('success', `🧠 Indexed ${result.inserted_items_count || 0} chunks into vector DB`);
    } catch (err) {
      onToast?.('error', `Indexing failed: ${err.message}`);
    } finally {
      setIndexing(false);
    }
  }, [projectId, onToast]);

  const handleRemoveFile = useCallback((fileId) => {
    setFiles(prev => prev.filter(f => f.fileId !== fileId));
  }, []);

  return (
    <DashboardPanel
      title="📚 Knowledge Base"
      loading={false}
      error={null}
      isEmpty={false}
    >
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept=".pdf,.txt,.md,.docx,.csv"
        onChange={handleUpload}
      />

      {/* Upload area */}
      <motion.button
        whileHover={{ scale: 1.01 }}
        whileTap={{ scale: 0.99 }}
        onClick={() => fileInputRef.current?.click()}
        disabled={uploading}
        className="w-full flex flex-col items-center justify-center gap-2 py-8 rounded-xl border-2 border-dashed border-white/10 bg-white/[0.02] text-white/35 hover:border-[#00f0ff]/30 hover:text-[#00f0ff]/60 hover:bg-[#00f0ff]/[0.02] transition-all cursor-pointer disabled:opacity-40"
      >
        {uploading ? (
          <Loader2 className="w-8 h-8 animate-spin" />
        ) : (
          <Upload className="w-8 h-8" />
        )}
        <span className="text-sm font-medium">
          {uploading ? 'Uploading…' : 'Drop a file or click to upload'}
        </span>
        <span className="text-[10px] text-white/20">
          PDF, TXT, MD, DOCX, CSV
        </span>
      </motion.button>

      {/* Files list */}
      {files.length > 0 && (
        <div className="mt-4 space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">
            Uploaded Files
          </p>
          {files.map((f, i) => (
            <div
              key={f.fileId || i}
              className="flex items-center gap-3 p-3 rounded-lg bg-white/[0.03] border border-white/[0.06]"
            >
              <FileText className="w-4 h-4 text-[#00f0ff]/60 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-white/70 m-0 truncate">{f.name}</p>
                <span className="text-[10px] text-white/25">{f.size}</span>
              </div>
              <span className={`text-[10px] px-2 py-0.5 rounded-full border ${
                f.status === 'processed'
                  ? 'bg-emerald-500/10 text-emerald-400/70 border-emerald-500/20'
                  : 'bg-yellow-500/10 text-yellow-400/70 border-yellow-500/20'
              }`}>
                {f.status === 'processed' ? (
                  <span className="flex items-center gap-1">
                    <CheckCircle2 className="w-3 h-3" /> Processed
                  </span>
                ) : f.status}
              </span>
              <button
                onClick={() => handleRemoveFile(f.fileId)}
                className="p-1 rounded-md hover:bg-red-500/10 text-white/20 hover:text-red-400 transition-colors cursor-pointer bg-transparent border-none"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Push to Vector DB button */}
      {files.length > 0 && (
        <motion.button
          whileHover={{ scale: 1.01 }}
          whileTap={{ scale: 0.98 }}
          onClick={handlePushIndex}
          disabled={indexing}
          className="mt-3 w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-gradient-to-r from-[#00f0ff]/10 to-[#a855f7]/10 border border-[#00f0ff]/20 text-[#00f0ff]/80 text-sm font-medium hover:from-[#00f0ff]/15 hover:to-[#a855f7]/15 transition-all cursor-pointer disabled:opacity-40"
        >
          {indexing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Database className="w-4 h-4" />
          )}
          {indexing ? 'Indexing…' : 'Push to Vector DB'}
        </motion.button>
      )}
    </DashboardPanel>
  );
}
