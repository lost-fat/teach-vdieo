import {
  AbsoluteFill,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

// Word-level caption for TikTok-style highlight display
export interface WordCaption {
  word: string;
  startMs: number;
  endMs: number;
}

export interface TranslationCaption {
  text: string;
  startMs: number;
  endMs: number;
}

export interface CaptionGroup {
  id: string;
  startMs: number;
  endMs: number;
  startWordIndex?: number;
  endWordIndex?: number;
  lineBreakAfterWordIndices?: number[];
  translationText?: string;
}

interface CaptionOverlayProps {
  words: WordCaption[];
  translations?: TranslationCaption[];
  groups?: CaptionGroup[];
  // How many words to show at once in a "page"
  wordsPerPage?: number;
  fontSize?: number;
  translationFontSize?: number;
  color?: string;
  translationColor?: string;
  highlightColor?: string;
  backgroundColor?: string;
  fontFamily?: string;
}

interface CaptionPage {
  words: WordCaption[];
  wordIndices: number[];
  startMs: number;
  endMs: number;
  lineBreakAfterWordIndices: number[];
  translationText?: string;
}

function buildPages(
  words: WordCaption[],
  wordsPerPage: number,
  groups: CaptionGroup[]
): CaptionPage[] {
  if (groups.length > 0) {
    return groups.flatMap((group) => {
      const hasWordRange =
        Number.isInteger(group.startWordIndex) &&
        Number.isInteger(group.endWordIndex);
      const startWordIndex = hasWordRange ? group.startWordIndex! : 0;
      const endWordIndex = hasWordRange ? group.endWordIndex! : words.length;
      const indexedWords = words
        .map((word, index) => ({word, index}))
        .filter(({word, index}) =>
          hasWordRange
            ? index >= startWordIndex && index < endWordIndex
            : word.startMs >= group.startMs && word.startMs < group.endMs
        );
      const pageWords = indexedWords.map(({word}) => word);
      if (pageWords.length === 0) return [];
      return [{
        words: pageWords,
        wordIndices: indexedWords.map(({index}) => index),
        startMs: group.startMs,
        endMs: group.endMs,
        lineBreakAfterWordIndices: group.lineBreakAfterWordIndices ?? [],
        translationText: group.translationText,
      }];
    });
  }

  const pages: CaptionPage[] = [];
  for (let i = 0; i < words.length; i += wordsPerPage) {
    const pageWords = words.slice(i, i + wordsPerPage);
    if (pageWords.length === 0) continue;
    pages.push({
      words: pageWords,
      wordIndices: pageWords.map((_, offset) => i + offset),
      startMs: pageWords[0].startMs,
      endMs: pageWords[pageWords.length - 1].endMs,
      lineBreakAfterWordIndices: [],
    });
  }
  return pages;
}

const PageRenderer: React.FC<{
  page: CaptionPage;
  fontSize: number;
  color: string;
  highlightColor: string;
  backgroundColor: string;
  fontFamily: string;
  translations: TranslationCaption[];
  translationFontSize: number;
  translationColor: string;
}> = ({
  page,
  fontSize,
  color,
  highlightColor,
  backgroundColor,
  fontFamily,
  translations,
  translationFontSize,
  translationColor,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const currentMs = page.startMs + (frame / fps) * 1000;
  const translation = translations.find(
    (item) => item.startMs <= currentMs && item.endMs > currentMs
  );
  const translationText = page.translationText ?? translation?.text;

  // Spring entrance
  const entrance = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120 },
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 80,
      }}
    >
      <div
        style={{
          opacity: entrance,
          transform: `translateY(${interpolate(entrance, [0, 1], [20, 0])}px)`,
          backgroundColor,
          borderRadius: 12,
          padding: "12px 24px",
          maxWidth: "68%",
          textAlign: "center",
          boxSizing: "border-box",
        }}
      >
        <span
          style={{
            display: "block",
            fontSize,
            fontWeight: 700,
            fontFamily,
            lineHeight: 1.4,
            whiteSpace: "pre-wrap",
          }}
        >
          {page.words.map((w, i) => {
            const isActive = w.startMs <= currentMs && w.endMs > currentMs;
            const isPast = w.endMs <= currentMs;
            const shouldBreak = page.lineBreakAfterWordIndices.includes(
              page.wordIndices[i]
            );
            return (
              <span
                key={`${w.startMs}-${i}`}
                style={{
                  color: isActive ? highlightColor : isPast ? color : `${color}99`,
                  transition: "none", // CSS transitions forbidden in Remotion
                  textShadow: isActive
                    ? `0 0 20px ${highlightColor}66, 0 2px 4px rgba(0,0,0,0.5)`
                    : "0 2px 4px rgba(0,0,0,0.5)",
                }}
              >
                <span>{w.word}</span>
                {shouldBreak ? <br /> : i < page.words.length - 1 ? " " : ""}
              </span>
            );
          })}
        </span>
        {translationText ? (
          <div
            style={{
              color: translationColor,
              fontFamily:
                '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif',
              fontSize: translationFontSize,
              fontWeight: 500,
              lineHeight: 1.45,
              letterSpacing: "0.01em",
              marginTop: 6,
              marginLeft: "auto",
              marginRight: "auto",
              maxWidth: "20em",
              whiteSpace: "pre-line",
              overflowWrap: "anywhere",
              opacity: 0.9,
              textShadow: "0 2px 4px rgba(0,0,0,0.55)",
            }}
          >
            {translationText}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

export const CaptionOverlay: React.FC<CaptionOverlayProps> = ({
  words,
  translations = [],
  groups = [],
  wordsPerPage = 6,
  fontSize = 38,
  translationFontSize = 26,
  color = "#F8FAFC",
  translationColor = "#FFFDF8",
  highlightColor = "#22D3EE",
  backgroundColor = "rgba(15, 23, 42, 0.75)",
  fontFamily = "Space Grotesk, Inter, system-ui, sans-serif",
}) => {
  const { fps } = useVideoConfig();
  const pages = buildPages(words, wordsPerPage, groups);

  return (
    <AbsoluteFill>
      {pages.map((page, i) => {
        const fromFrame = Math.round((page.startMs / 1000) * fps);
        const nextStart = pages[i + 1]?.startMs ?? page.endMs + 500;
        const duration = Math.max(
          1,
          Math.round(((nextStart - page.startMs) / 1000) * fps)
        );

        return (
          <Sequence
            key={i}
            from={fromFrame}
            durationInFrames={duration}
            premountFor={fps}
          >
            <PageRenderer
              page={page}
              fontSize={fontSize}
              color={color}
              highlightColor={highlightColor}
              backgroundColor={backgroundColor}
              fontFamily={fontFamily}
              translations={translations}
              translationFontSize={translationFontSize}
              translationColor={translationColor}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
