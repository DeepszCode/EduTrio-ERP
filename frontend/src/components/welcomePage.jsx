export default function WelcomePage({ onStart }) {
  return (
    <main className="welcome-page">
      {/* LEFT BRANDING PANEL */}
      <section className="brand-panel">
        <div className="brand-content">

          {/* Education Logo */}
          <div className="education-logo">
            <svg
              viewBox="0 0 180 150"
              role="img"
              aria-label="SMART ERP education logo"
            >
              {/* Graduation cap */}
              <path
                d="M18 38 L90 7 L162 38 L90 70 Z"
                fill="#FFFFFF"
              />
              <path
                d="M42 57 L42 81 Q90 112 138 81 L138 57
                   L90 81 Z"
                fill="#FFFFFF"
              />
              <path
                d="M153 43 L153 74"
                stroke="#FBBF24"
                strokeWidth="5"
                strokeLinecap="round"
              />
              <circle cx="153" cy="78" r="5" fill="#FBBF24" />

              {/* Open book */}
              <path
                d="M22 83 L22 128 Q59 127 88 147
                   L88 104 Q59 82 22 83 Z"
                fill="none"
                stroke="#FFFFFF"
                strokeWidth="7"
                strokeLinejoin="round"
              />
              <path
                d="M158 83 L158 128 Q121 127 92 147
                   L92 104 Q121 82 158 83 Z"
                fill="none"
                stroke="#FFFFFF"
                strokeWidth="7"
                strokeLinejoin="round"
              />
              <path
                d="M31 94 L31 121 M149 94 L149 121"
                stroke="#77B8FF"
                strokeWidth="3"
                strokeLinecap="round"
              />

              {/* Gold star */}
              <path
                d="M90 102 L94 111 L104 112 L97 119
                   L99 129 L90 124 L81 129 L83 119
                   L76 112 L86 111 Z"
                fill="#FBBF24"
              />
            </svg>
          </div>

          {/* Project title */}
          <h1 className="brand-title">SMART ERP</h1>

          <p className="brand-subtitle">
            Certificate Data Extraction
            <br />
            and Management System
          </p>

          {/* Features */}
          <div className="feature-list">
            <span>Scan</span>
            <i />
            <span>Extract</span>
            <i />
            <span>Manage</span>
            <i />
            <span>Secure</span>
          </div>

          <p className="brand-tagline">
            Turning Documents into
            <br />
            Digital Opportunities
          </p>
        </div>

        {/* Decorative blue waves */}
        <div className="wave wave-one" />
        <div className="wave wave-two" />
        <div className="wave wave-three" />
      </section>

      {/* RIGHT WELCOME PANEL */}
      <section className="welcome-panel">
        <div className="campus-background">
          <CampusIllustration />
        </div>

        <div className="welcome-content">
          {/* Graduation books illustration */}
          <div className="graduation-art">
            <GraduationIllustration />
          </div>

          <p className="welcome-eyebrow">WELCOME TO</p>

          <h2 className="welcome-title">SMART ERP</h2>

          <p className="welcome-description">
            Your all-in-one solution for certificate data
            <br className="desktop-break" />
            extraction and management.
          </p>

          <button
            type="button"
            className="get-started-btn"
            onClick={onStart}
          >
            <span>Get Started</span>
            <span className="button-arrow" aria-hidden="true">
              →
            </span>
          </button>
        </div>
      </section>

      {/* BOTTOM FOOTER */}
      <footer className="site-footer">
        <svg
          viewBox="0 0 48 40"
          className="footer-logo"
          aria-hidden="true"
        >
          <path
            d="M24 2 L44 11 L24 20 L4 11 Z"
            fill="#FFFFFF"
          />
          <path
            d="M7 18 V30 Q17 29 23 37 V23
               Q15 17 7 18 Z"
            fill="none"
            stroke="#FFFFFF"
            strokeWidth="3"
          />
          <path
            d="M41 18 V30 Q31 29 25 37 V23
               Q33 17 41 18 Z"
            fill="none"
            stroke="#FFFFFF"
            strokeWidth="3"
          />
        </svg>

        <span>
          SMART ERP CERTIFICATE DATA EXTRACTION AND
          MANAGEMENT SYSTEM
        </span>
      </footer>
    </main>
  );
}

/* Faded college building illustration */
function CampusIllustration() {
  return (
    <svg
      className="campus-svg"
      viewBox="0 0 900 450"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <g fill="#C9D4D9" stroke="#BAC8CF" strokeWidth="5">
        {/* Main roof */}
        <path d="M170 180 L450 25 L730 180 Z" />
        <rect x="205" y="180" width="490" height="175" />

        {/* Central tower */}
        <path d="M390 65 L450 12 L510 65 V180 H390 Z" />
        <rect x="420" y="85" width="60" height="95" />

        {/* Left wing */}
        <path d="M35 225 L170 155 L205 225 Z" />
        <rect x="50" y="225" width="155" height="130" />

        {/* Right wing */}
        <path d="M695 225 L765 175 L865 225 Z" />
        <rect x="695" y="225" width="155" height="130" />
      </g>

      {/* Columns */}
      {[240, 325, 410, 495, 580, 665].map((x) => (
        <g key={x}>
          <rect
            x={x}
            y="195"
            width="18"
            height="160"
            fill="#E5EBEE"
          />
          <rect
            x={x + 28}
            y="195"
            width="18"
            height="160"
            fill="#E5EBEE"
          />
        </g>
      ))}

      {/* Windows */}
      {[75, 120, 735, 790, 835].map((x) => (
        <rect
          key={x}
          x={x}
          y="250"
          width="24"
          height="42"
          rx="3"
          fill="#A8BAC5"
        />
      ))}

      {/* Entrance */}
      <path
        d="M390 355 V265 Q450 200 510 265 V355 Z"
        fill="#9EAFBB"
      />

      {/* Lawn and walkway */}
      <path d="M0 355 H900 V450 H0 Z" fill="#D7E2D4" />
      <path d="M385 355 H515 L580 450 H320 Z" fill="#E8E8E3" />

      {/* Trees */}
      {[85, 155, 750, 820].map((x, index) => (
        <g key={x} opacity="0.8">
          <rect
            x={x}
            y={index % 2 === 0 ? 280 : 300}
            width="12"
            height="75"
            fill="#A6B7A8"
          />
          <circle
            cx={x + 6}
            cy={index % 2 === 0 ? 270 : 290}
            r="38"
            fill="#B7C9B7"
          />
        </g>
      ))}
    </svg>
  );
}

/* Graduation cap and certificate books */
function GraduationIllustration() {
  return (
    <svg
      viewBox="0 0 460 230"
      className="graduation-svg"
      role="img"
      aria-label="Graduation cap resting on certificate books"
    >
      <defs>
        <linearGradient id="capShade" x1="0" x2="1">
          <stop offset="0%" stopColor="#11151D" />
          <stop offset="100%" stopColor="#343D4A" />
        </linearGradient>

        <linearGradient id="blueBook" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#DDEEFF" />
          <stop offset="100%" stopColor="#A9C7E4" />
        </linearGradient>
      </defs>

      {/* Ground shadow */}
      <ellipse
        cx="230"
        cy="207"
        rx="190"
        ry="12"
        fill="#D9E0E8"
        opacity="0.65"
      />

      {/* Bottom book */}
      <g>
        <rect
          x="55"
          y="157"
          width="340"
          height="36"
          rx="8"
          fill="#B66E36"
        />
        <rect
          x="72"
          y="157"
          width="315"
          height="27"
          rx="5"
          fill="#FFF8E9"
        />
        <path
          d="M75 184 H386"
          stroke="#D5CDBF"
          strokeWidth="2"
        />
      </g>

      {/* Middle book */}
      <g>
        <rect
          x="100"
          y="122"
          width="275"
          height="39"
          rx="8"
          fill="#B5D7F2"
        />
        <rect
          x="111"
          y="126"
          width="250"
          height="27"
          rx="4"
          fill="#FFFDF6"
        />
        <path
          d="M113 153 H360"
          stroke="#E2DDD3"
          strokeWidth="2"
        />
      </g>

      {/* Top book */}
      <g>
        <rect
          x="125"
          y="92"
          width="225"
          height="36"
          rx="7"
          fill="url(#blueBook)"
        />
        <rect
          x="137"
          y="96"
          width="201"
          height="24"
          rx="4"
          fill="#F8FBFF"
        />
      </g>

      {/* Graduation cap */}
      <path
        d="M94 76 L230 42 L366 76 L230 111 Z"
        fill="url(#capShade)"
      />
      <path
        d="M148 88 L155 125 Q230 149 305 125 L312 88
           Q230 108 148 88 Z"
        fill="#161C25"
      />
      <path
        d="M148 88 L155 125 Q230 149 305 125 L312 88"
        fill="none"
        stroke="#343D4A"
        strokeWidth="3"
      />

      {/* Gold tassel */}
      <path
        d="M325 66 L335 130"
        stroke="#E9AD22"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <path
        d="M335 130 L328 166 L343 166 L337 130 Z"
        fill="#F5B51B"
      />
      <circle cx="335" cy="130" r="5" fill="#F5B51B" />
    </svg>
  );
}