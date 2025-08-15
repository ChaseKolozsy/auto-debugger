// Minimal VS Code extension to test Roo communication
import * as vscode from 'vscode';
import * as net from 'net';

export function activate(context: vscode.ExtensionContext) {
    console.log('Roo Bridge Test activated');
    
    // Create a simple TCP server
    const server = net.createServer((socket) => {
        console.log('Python client connected!');
        
        // When we receive data from Python
        socket.on('data', (data) => {
            const message = data.toString().trim();
            console.log('Received from Python:', message);
            
            // Simple test: If Python sends "ping", we respond "pong"
            if (message === 'ping') {
                socket.write('pong\n');
                
                // Also show a VS Code notification
                vscode.window.showInformationMessage('Roo Bridge: Received ping from Python!');
            }
            else if (message.startsWith('roo:')) {
                // Try to send to Roo (if available)
                const rooMessage = message.substring(4);
                
                // Create or focus terminal
                const terminal = vscode.window.activeTerminal || vscode.window.createTerminal('Roo Test');
                terminal.show();
                terminal.sendText(`@roo ${rooMessage}`);
                
                socket.write('sent_to_terminal\n');
            }
        });
    });
    
    server.listen(9999, '127.0.0.1', () => {
        vscode.window.showInformationMessage('Roo Bridge Test: Listening on port 9999');
    });
    
    context.subscriptions.push({
        dispose: () => server.close()
    });
}

export function deactivate() {}