import './globals.css'
import type { ReactNode } from 'react'
import Shell from '@/components/warranty/Shell'

export const metadata = { title: 'Warranty Desk — agents on Autoline data vs OEM rules (simulated OEM gateway)' }

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  )
}
