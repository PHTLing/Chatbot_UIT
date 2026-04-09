import { useState, useEffect, useRef } from 'react';
import uitLogo from './assets/logo_uit.webp';
import './App.css';
import { Send, Paperclip, Smile, MoreHorizontal, GraduationCap, LayoutGrid, Building2, Briefcase } from 'lucide-react';

interface Message {
  role: 'user' | 'bot';
  text: string;
}
interface Message {
  role: 'user' | 'bot';
  text: string;
}

const App = () => {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [chatHistory, setChatHistory] = useState<{role: string, content: string}[]>([]);


  // Tự động cuộn xuống cuối khi có tin nhắn mới
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async (textToSend?: string) => {
    const messageText = textToSend || input;
    if (!messageText.trim() || loading) return;

    // Thêm tin nhắn của User vào giao diện
    const userMsg: Message = { role: 'user', text: messageText };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    // Chuẩn bị lịch sử gửi đi (lấy tin nhắn mới + lịch sử cũ)
    const newUserHistory = { role: 'user', content: messageText };
    const updatedHistory = [...chatHistory, newUserHistory].slice(-6);

    try {
      const response = await fetch('http://localhost:8000/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          query: messageText,
          history: updatedHistory
        }),
      });
      const data = await response.json();
      
      // Thêm phản hồi từ Bot
      const botReply = data.reply;
      setMessages((prev) => [...prev, { role: 'bot', text: botReply }]);
      const botHistoryMsg = { role: 'assistant', content: botReply };
      setChatHistory([...updatedHistory, botHistoryMsg]);
    } catch (error) {
      setMessages((prev) => [...prev, { role: 'bot', text: 'Lỗi kết nối server Backend!' }]);
    } finally {
      setLoading(false);
    }
  };

  const suggestions = [
    { icon: <GraduationCap size={20} />, text: 'Thông tin tuyển sinh năm 2025?' },
    { icon: <LayoutGrid size={20} />, text: 'Các ngành đào tạo tại UIT?' },
    { icon: <Building2 size={20} />, text: 'Cơ sở vật chất của trường?' },
    { icon: <Briefcase size={20} />, text: 'Cơ hội việc làm sau tốt nghiệp?' },
  ];
 
  return (
    <div className="chatbot-wrapper">
      {/* Header */}
      <header className="chat-header">
        <div className="header-left">
          <div className="logo-container">
            <img src={uitLogo} alt="UIT Logo" className="header-logo" />
            <div className="status-dot"></div>
          </div>
          <div className="header-info">
            <h3>UIT Chatbot</h3>
            <span className="status-text">Trực tuyến</span>
          </div>
        </div>
        <button className="menu-btn"><MoreHorizontal size={20} /></button>
      </header>

      {/* Main Content */}
      <main className="chat-main">
        {messages.length === 0 ? (
          <div className="welcome-screen">
            <div className="big-logo">
              <img src={uitLogo} alt="UIT Logo" />
            </div>
            <h1>Xin chào! 👋</h1>
            <p className="subtitle">Tôi là trợ lý ảo của Trường Đại học Công nghệ Thông tin - ĐHQG HCM. Tôi có thể giúp gì cho bạn?</p>
            
            <div className="suggestion-grid">
              {suggestions.map((item, index) => (
          <button key={index} className="suggestion-card" onClick={() => handleSend(item.text)}>
            <div className="card-icon">{item.icon}</div>
            <span>{item.text}</span>
          </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-history">
            {messages.map((m, i) => (
              <div key={i} className={`message-row ${m.role}`}>
          <div className="message-bubble">
            {m.role === 'bot' ? (
              <div dangerouslySetInnerHTML={{ __html: m.text }} />
            ) : (
              m.text
            )}
          </div>
              </div>
            ))}
            {loading && (
              <div className="message-row bot">
          <div className="message-bubble typing-indicator">
            <span></span>
            <span></span>
            <span></span>
          </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
        )}
      </main>

      {/* Footer / Input */}
      <footer className="chat-footer">
        <div className="input-container">
          <button className="icon-btn"><Paperclip size={20} /></button>
          <input 
            type="text" 
            placeholder="Nhập tin nhắn..." 
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          />
          <button className="icon-btn"><Smile size={20} /></button>
          <button className="send-btn" onClick={() => handleSend()} disabled={loading}>
            <Send size={20} />
          </button>
        </div>
        <p className="disclaimer">UIT Chatbot có thể mắc lỗi. Hãy kiểm tra thông tin quan trọng.</p>
      </footer>
    </div>
  );
};

export default App;