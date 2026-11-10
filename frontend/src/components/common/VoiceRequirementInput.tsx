import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Spin, Typography } from 'antd';
import { AudioOutlined, AudioMutedOutlined, TranslationOutlined } from '@ant-design/icons';
import { translateRequirementText } from '../../api/aiApi';

const { Text } = Typography;

type SpeechAlternative = { transcript: string };
type SpeechResult = ArrayLike<SpeechAlternative> & { isFinal: boolean };
type SpeechResultEvent = Event & {
  resultIndex: number;
  results: ArrayLike<SpeechResult>;
};
type SpeechErrorEvent = Event & { error: string };
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: ((event: SpeechErrorEvent) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;
type SpeechWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};
type SpeechLanguage = 'ta-IN' | 'hi-IN' | 'en-IN';

interface VoiceRequirementInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

const languageOptions: { value: SpeechLanguage; label: string }[] = [
  { value: 'ta-IN', label: 'Tamil (India)' },
  { value: 'hi-IN', label: 'Hindi (India)' },
  { value: 'en-IN', label: 'English (India)' },
];

const appendText = (base: string, addition: string): string => {
  const cleanAddition = addition.trim();
  if (!cleanAddition) return base;
  const separator = base && !/\s$/.test(base) ? ' ' : '';
  return `${base}${separator}${cleanAddition}`;
};

const replaceAppendedText = (base: string, previous: string, next: string): string => {
  const previousIndex = previous ? base.lastIndexOf(previous) : -1;
  if (previousIndex < 0) return appendText(base, next);
  return `${base.slice(0, previousIndex)}${next}${base.slice(previousIndex + previous.length)}`;
};

const errorMessage = (code: string): string => {
  if (code === 'not-allowed' || code === 'service-not-allowed') {
    return 'Microphone access was denied. Allow microphone access in your browser settings, then try again.';
  }
  if (code === 'audio-capture') return 'No microphone is available. Connect a microphone and try again.';
  if (code === 'language-not-supported') return 'Speech recognition is not available for this language in this browser.';
  if (code === 'network') return 'Speech recognition could not connect. Check your connection and try again.';
  return 'Speech recognition stopped unexpectedly. You can try recording again.';
};

export const VoiceRequirementInput: React.FC<VoiceRequirementInputProps> = ({
  value,
  onChange,
  disabled = false,
}) => {
  const [language, setLanguage] = useState<SpeechLanguage>('en-IN');
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const [originalText, setOriginalText] = useState('');
  const [translatedText, setTranslatedText] = useState('');
  const [translating, setTranslating] = useState(false);
  const [translationError, setTranslationError] = useState<string | null>(null);
  const [translatedSource, setTranslatedSource] = useState('');
  const [speechError, setSpeechError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const startRequestRef = useRef(0);
  const translationRequestRef = useRef(0);
  const mountedRef = useRef(true);
  const baseTextRef = useRef('');
  const baseOriginalTextRef = useRef('');
  const finalTranscriptRef = useRef('');

  const speechWindow = typeof window === 'undefined' ? null : window as SpeechWindow;
  const Recognition = speechWindow?.SpeechRecognition ?? speechWindow?.webkitSpeechRecognition;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      startRequestRef.current += 1;
      translationRequestRef.current += 1;
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      if (recognition) {
        recognition.onresult = null;
        recognition.onerror = null;
        recognition.onend = null;
        recognition.abort();
      }
    };
  }, []);

  const translateTranscript = async (
    transcript: string,
    sourceLanguage: 'ta-IN' | 'hi-IN',
    baseText: string,
    displayedTranscript = transcript
  ): Promise<void> => {
    const requestId = ++translationRequestRef.current;
    setTranslationError(null);
    setTranslating(true);
    try {
      const translated = await translateRequirementText(transcript, sourceLanguage);
      if (!mountedRef.current || requestId !== translationRequestRef.current) return;
      onChange(replaceAppendedText(value || baseText, translatedText, translated));
      setTranslatedText(translated);
      setTranslatedSource(displayedTranscript.trim());
    } catch {
      if (mountedRef.current && requestId === translationRequestRef.current) {
        setTranslationError('Translation failed. Check your connection and retry.');
      }
    } finally {
      if (mountedRef.current && requestId === translationRequestRef.current) {
        setTranslating(false);
      }
    }
  };

  const stopRecording = (): void => {
    startRequestRef.current += 1;
    const recognition = recognitionRef.current;
    if (recognition) {
      recognition.stop();
    } else {
      setStarting(false);
    }
  };

  const startRecording = async (): Promise<void> => {
    if (!Recognition) {
      setSpeechError('Voice input is not supported in this browser. You can still type your requirements.');
      return;
    }
    const requestId = ++startRequestRef.current;
    setSpeechError(null);
    setTranslationError(null);
    setTranslatedText('');
    setStarting(true);

    try {
      if (navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
      }
      if (!mountedRef.current || requestId !== startRequestRef.current) return;

      const recognition = new Recognition();
      baseTextRef.current = value;
      baseOriginalTextRef.current = originalText;
      finalTranscriptRef.current = '';
      recognition.lang = language;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onresult = (event) => {
        let finalText = '';
        let interimText = '';
        for (let index = 0; index < event.results.length; index += 1) {
          const result = event.results[index];
          const transcript = result[0]?.transcript || '';
          if (result.isFinal) finalText += transcript;
          else interimText += transcript;
        }
        finalTranscriptRef.current = finalText;
        setOriginalText(appendText(
          baseOriginalTextRef.current,
          `${finalText}${interimText}`
        ));
      };
      recognition.onerror = (event) => {
        setSpeechError(errorMessage(event.error));
        setRecording(false);
        setStarting(false);
        recognitionRef.current = null;
      };
      recognition.onend = () => {
        const finalText = finalTranscriptRef.current.trim();
        const completeTranscript = appendText(baseOriginalTextRef.current, finalText).trim();
        setOriginalText(completeTranscript);
        setRecording(false);
        setStarting(false);
        recognitionRef.current = null;
        if (!finalText) return;

        if (language === 'en-IN') {
          onChange(appendText(baseTextRef.current, finalText));
          setTranslatedSource(completeTranscript);
        } else {
          void translateTranscript(
            finalText,
            language,
            baseTextRef.current,
            completeTranscript
          );
        }
      };
      recognitionRef.current = recognition;
      recognition.start();
      setRecording(true);
      setStarting(false);
    } catch (reason) {
      if (!mountedRef.current || requestId !== startRequestRef.current) return;
      const errorName = reason instanceof DOMException ? reason.name : '';
      setSpeechError(errorName === 'NotAllowedError' || errorName === 'SecurityError'
        ? errorMessage('not-allowed')
        : errorName === 'NotFoundError'
          ? errorMessage('audio-capture')
          : 'Unable to start speech recognition. Check microphone permission and try again.');
      setStarting(false);
    }
  };

  const retryTranslation = (): void => {
    const transcript = originalText.trim();
    if (!transcript || language === 'en-IN') return;
    void translateTranscript(transcript, language, baseTextRef.current);
  };

  const translationNeedsRetry = language !== 'en-IN'
    && Boolean(originalText.trim())
    && (Boolean(translationError) || translatedSource !== originalText.trim());
  const translatedFrom = language === 'ta-IN' ? 'Tamil' : 'Hindi';

  return (
    <div className="ai-voice-input">
      <div className="ai-voice-controls">
        <label className="ai-voice-language">
          <span>Speaking language</span>
          <Select
            aria-label="Speaking language"
            value={language}
            options={languageOptions}
            onChange={(nextLanguage: SpeechLanguage) => {
              setLanguage(nextLanguage);
              setSpeechError(null);
              if (nextLanguage === 'en-IN') setTranslatedText('');
            }}
            disabled={disabled || recording || starting || translating}
          />
        </label>
        <div className="ai-voice-action">
          <Button
            className={`ai-microphone-button${recording ? ' is-recording' : ''}`}
            icon={recording ? <AudioMutedOutlined /> : <AudioOutlined />}
            disabled={disabled || starting || translating}
            onClick={recording ? stopRecording : () => void startRecording()}
          >
            {recording ? 'Stop Recording' : 'Start Speaking'}
          </Button>
          {recording ? (
            <div className="ai-voice-live" aria-live="polite">
              <span className="ai-voice-wave" aria-hidden="true">
                <i /><i /><i /><i /><i />
              </span>
              <span className="ai-voice-recording-dot" />
              <Text>Listening...</Text>
            </div>
          ) : starting ? (
            <Text className="ai-voice-status" type="secondary">Requesting microphone permission...</Text>
          ) : null}
          {translating && (
            <Text className="ai-voice-status" type="secondary"><Spin size="small" /> Translating to English...</Text>
          )}
        </div>
      </div>
      {!Recognition && (
        <Text className="ai-voice-status ai-voice-fallback" type="secondary">
          Voice input is not supported in this browser. You can still type your requirements.
        </Text>
      )}
      {Boolean(originalText) && (
        <div className="ai-voice-previews">
          <section className="ai-voice-preview">
            <div className="ai-voice-preview-heading">
              <Text className="ai-voice-transcript-label">Original Speech</Text>
              <Text type="secondary">Edit the transcription before translating</Text>
            </div>
            <Input.TextArea
              aria-label="Original speech transcription"
              autoSize={{ minRows: 2, maxRows: 5 }}
              value={originalText}
              onChange={(event) => {
                const editedTranscript = event.target.value;
                setOriginalText(editedTranscript);
                if (language === 'en-IN') {
                  onChange(appendText(baseTextRef.current, editedTranscript));
                  setTranslatedSource(editedTranscript.trim());
                }
              }}
              disabled={recording || translating || disabled}
            />
            {translationNeedsRetry && !translating && (
              <Button
                className="ai-voice-retry"
                size="small"
                icon={<TranslationOutlined />}
                onClick={retryTranslation}
                disabled={disabled}
              >
                {translationError ? 'Retry translation' : 'Translate edited text'}
              </Button>
            )}
          </section>
          {language !== 'en-IN' && Boolean(translatedText) && (
            <section className="ai-voice-preview ai-voice-preview--translation">
              <div className="ai-voice-preview-heading">
                <Text className="ai-voice-transcript-label">
                  <TranslationOutlined /> English Translation
                </Text>
                <Text type="secondary">Translated from {translatedFrom}</Text>
              </div>
              <Input.TextArea
                aria-label="Editable English translation"
                autoSize={{ minRows: 2, maxRows: 5 }}
                value={translatedText}
                onChange={(event) => {
                  const editedTranslation = event.target.value;
                  setTranslatedText(editedTranslation);
                  onChange(replaceAppendedText(value, translatedText, editedTranslation));
                }}
                disabled={translating || disabled}
              />
            </section>
          )}
        </div>
      )}
      {speechError && <Alert className="ai-voice-error" type="error" showIcon message={speechError} />}
      {translationError && (
        <Alert className="ai-voice-error" type="error" showIcon message={translationError} />
      )}
    </div>
  );
};

export default VoiceRequirementInput;
