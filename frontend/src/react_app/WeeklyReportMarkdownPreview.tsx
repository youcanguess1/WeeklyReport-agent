import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  h1: ({ children }) => <h1 className="mb-6 text-[24px] font-bold leading-9 text-[#172033] sm:text-[26px]">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-9 mb-3 border-b border-[#e6e9ee] pb-2.5 text-[17px] font-bold leading-7 text-[#21324a]">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-7 mb-3 text-[15px] font-bold leading-7 text-[#263c57]">{children}</h3>,
  p: ({ children }) => <p className="my-3 text-[14px] leading-7 text-[#334155]">{children}</p>,
  ul: ({ children }) => <ul className="my-3.5 list-disc space-y-2 pl-5 text-[14px] leading-7 text-[#334155]">{children}</ul>,
  ol: ({ children }) => <ol className="my-3.5 list-decimal space-y-2 pl-5 text-[14px] leading-7 text-[#334155]">{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  strong: ({ children }) => <strong className="font-bold text-[#1f334c]">{children}</strong>,
  blockquote: ({ children }) => <blockquote className="my-4 border-l-3 border-[#91a8c2] bg-[#f5f7fa] px-4 py-2 text-[#516276]">{children}</blockquote>,
  hr: () => <hr className="my-6 border-0 border-t border-[#e6e9ee]" />,
  table: ({ children }) => <div className="my-5 overflow-x-auto rounded-lg border border-[#dfe5ec]"><table className="w-full min-w-[520px] border-collapse text-left text-[12.5px] leading-6">{children}</table></div>,
  thead: ({ children }) => <thead className="bg-[#f3f6f9] text-[#253b55]">{children}</thead>,
  th: ({ children }) => <th className="border-b border-[#dfe5ec] px-3.5 py-2.5 font-bold">{children}</th>,
  td: ({ children }) => <td className="border-b border-[#edf0f3] px-3.5 py-2.5 align-top text-[#405166] last:border-b-0">{children}</td>,
  img: ({ src, alt }) => src ? <figure className="my-4 overflow-hidden rounded-lg border border-[#dfe5ec] bg-[#f8fafc] p-2"><img src={src} alt={alt || "证据截图"} loading="lazy" className="mx-auto block max-h-[360px] w-auto max-w-full rounded object-contain" /><figcaption className="px-1 pt-2 text-center text-[11px] text-[#8391a2]">{alt || "证据截图"}</figcaption></figure> : null,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="text-[#2563eb] underline decoration-[#bfdbfe] underline-offset-2 hover:text-[#1d4ed8]">{children}</a>,
  code: ({ children }) => <code className="rounded bg-[#f1f4f7] px-1.5 py-0.5 font-mono text-[12px] text-[#b45309]">{children}</code>,
};

export function WeeklyReportMarkdownPreview({
  markdown,
  evidenceImages = [],
}: {
  markdown: string;
  evidenceImages?: { material_id: string; url: string }[];
}) {
  return (
    <article className="weekly-report-markdown-preview">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
      {evidenceImages.length > 0 && (
        <section aria-label="参考图片" className="mt-8 border-t border-[#e6e9ee] pt-4">
          <h2 className="text-[17px] font-bold">参考图片</h2>
          {evidenceImages.map((image) => (
            <figure key={image.material_id} className="my-4">
              <img src={image.url} alt="周报参考图片" loading="lazy" className="max-h-[360px] max-w-full rounded object-contain" />
            </figure>
          ))}
        </section>
      )}
    </article>
  );
}
